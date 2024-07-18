import fcntl
import subprocess
import json
import base64
import sys
import os
from dockerspawner import SwarmSpawner, DockerSpawner
from traitlets import Unicode, List, validate, TraitError
from tornado import web
from oauthenticator.generic import GenericOAuthenticator
from oauthenticator.oauth2 import OAuthLoginHandler, OAuthenticator, OAuthLogoutHandler

def _serialize_state(state):
    """Serialize OAuth state to a base64 string after passing through JSON"""
    json_state = json.dumps(state)
    return base64.urlsafe_b64encode(json_state.encode("utf8")).decode("ascii")

class ShibbolethClerkLoginHandler(OAuthLoginHandler):

    def _get_user_data_from_request(self):
        """Get shibboleth attributes (user data) from request headers."""
        user_data = {'shibboleth':False}
        value_list = [self.request.headers.get(header, "") for header in self.authenticator.headers]
        self.log.info("User data debug: %s", (value_list) )
        if value_list[0]:
                user_data['jh_name'] = 'shibboleth-' + value_list[0]
                user_data['shibboleth'] = True
        self.log.info("User data debug: %s", user_data)

        return user_data

    async def get(self):
        """Get user data and log user in."""
        self.statsd.incr('login.request')
        user_data = self._get_user_data_from_request()

        if user_data['shibboleth']:
            user = await self.login_user(user_data)
            if user is None:
                raise web.HTTPError(403)
            else:
                self.redirect(self.get_next_url(user))

        else:
            redirect_uri = self.authenticator.get_callback_url(self)
            token_params = self.authenticator.extra_authorize_params.copy()
            self.log.info(f"OAuth redirect: {redirect_uri}")

            state_id = self._generate_state_id()
            next_url = self._get_next_url()
            cookie_state = _serialize_state({"state_id": state_id, "next_url": next_url})
            self.set_state_cookie(cookie_state)

            authorize_state = _serialize_state({"state_id": state_id})
            token_params["state"] = authorize_state

            self.authorize_redirect(
                redirect_uri=redirect_uri,
                client_id=self.authenticator.client_id,
                scope=self.authenticator.scope,
                extra_params=token_params,
                response_type="code",
            )

class ShibbolethClerkLogoutHandler(OAuthLogoutHandler):
    """Log a user out from JupyterHub by clearing their login cookie
    and then redirect to shibboleth logout url to clear shibboleth cookie."""

    async def get(self):
        user = await self.get_current_user()

        self.log.info("User logged out: %s", user.name)
        await self.default_handle_logout()
        await self.handle_logout()
        self._jupyterhub_user = None
        if user.name.startswith('shibboleth'):
            self.redirect(self.authenticator.shibboleth_logout_url)
        else:
            self.redirect("https://databrix.org")

class ShibbolethClerkAuthenticator(OAuthenticator):

    manage_groups = True

    headers = List(
        default_value=['mail'],
        config=True,
        help="""List of HTTP headers to get user data. First item is used as unique user name."""
    )
    shibboleth_logout_url = Unicode(
        default_value='',
        config=True,
        help="""Url to logout from shibboleth SP.""")

    login_handler = ShibbolethClerkLoginHandler
    logout_handler = ShibbolethClerkLogoutHandler


    def _initialize_user(self, username):

        with open('/srv/ngshare/dhbw-user-database/group_info.json', 'r') as file:
            groupinfo_dict = json.load(file)

        group = 0

        for k,v in groupinfo_dict.items():
            if len(v) < 4:
                groupinfo_dict[k].append(username)
                with open('/srv/ngshare/dhbw-user-database/group_info.json', 'w') as file:
                    fcntl.flock(file, fcntl.LOCK_EX)
                    json.dump(groupinfo_dict, file)
                    fcntl.flock(file, fcntl.LOCK_UN)
                userdata = {"username":username , "group": k}
                filepath = '/srv/ngshare/dhbw-user-database/' + username + '.json'
                with open(filepath, "w") as json_file:
                    json.dump(userdata, json_file)
                group = k

                break

        return group


    @validate('headers')
    def _valid_headers(self, proposal):
        if not proposal['value']:
            raise TraitError('Headers should contain at least 1 item.')
        return proposal['value']

    def login_url(self, base_url):
        return url_path_join(base_url, "login")
    async def authenticate(self, handler, data):


        try:
            check = data['shibboleth']
            username = data['jh_name']
            file_path = '/srv/ngshare/dhbw-user-database/' + username +'.json'
            if os.path.exists(file_path):

                with open(file_path, 'r') as file:
                    userinfo_dict = json.load(file)
                user_data = {
                    'name': username,
                    'auth_state': data,
                    'groups': [userinfo_dict['group']]
                    }

            else:
                group = self._initialize_user(username)
                jhname = username
                user_data = {
                    'name': jhname,
                    'auth_state': data,
                    'groups': [group]
                    }
            return user_data

        except:
            access_token_params = self.build_access_tokens_request_params(handler, data)
            token_info = await self.get_token_info(handler, access_token_params)
            user_info = await self.token_to_user(token_info)

            username = self.user_info_to_username(user_info)
            username = self.normalize_username(username)

            refresh_token = token_info.get("refresh_token", None)
            if self.enable_auth_state and not refresh_token:
                self.log.debug(
                    "Refresh token was empty, will try to pull refresh_token from previous auth_state"
                )
                refresh_token = await self.get_prev_refresh_token(handler, username)
                if refresh_token:
                    token_info["refresh_token"] = refresh_token

            auth_model = {
                "name": 'clerk-'+username,
                "admin": True if username in self.admin_users else None,
                "auth_state": self.build_auth_state_dict(token_info, user_info),
                "groups" : [user_info.get('public_metadata')['rolle']]
            }

            return await self.update_auth_model(auth_model)

    def get_handlers(self, app):
        return [ (r'/oauth_callback',self.callback_handler),
                 (r'/logout',self.logout_handler),
                 (r'/login', self.login_handler),
               ]

"""
---------------------------add idle culler service and user role----------------------------------------------------"""
c.JupyterHub.services = [
    {
        "name": "jupyterhub-idle-culler-service",
        "command": [
            sys.executable,
            "-m", "jupyterhub_idle_culler",
            "--timeout=240",
        ],
         "admin": True,
    }
]

c.JupyterHub.load_roles=[
    {
        "name": "user",
        "scopes": [
            "read:users!user",
            ]
    },
    {
        "name": "jupyterhub-idle-culler-role",
        "scopes": [
            "list:users",
            "read:users:activity",
            "read:servers",
            "delete:servers",
            "admin:users", # if using --cull-users
        ],
        "services": ["jupyterhub-idle-culler-service"],
    }
]
"""
--------------create collaborative workspaces and define the roles of group members-------------------------------->"""

dozent_scope = ["self"]
gruppe_scope = []
for i in [1,2,3,4,5,6,7,8,9,10]:

    group_name = 'dhbw-gruppe' + str(int(i))
    space_name = 'workspace_dhbw-gruppe' + str(int(i))
    gruppe_scope.append("servers!user=" + space_name)

    student_name = []
    student_name = ['student'+ '0'*( 3 - len(str(j+1)) ) + str(j+1) for j in range( (i-1)*3, i*3 )]

#    c.JupyterHub.load_groups[group_name] = {'users':[space_name]+ student_name}
    c.JupyterHub.load_roles.append(
        {
        "name": "dhbw-gruppen-"+str(i)+"-mitglied",
        "scopes": [
            "access:servers!user=" + space_name,
            "servers!user=" + space_name,
            "read:users!user=" + space_name,
            "users:activity!user=" + space_name,
            "list:users!user=" + space_name,
        ],
        "groups": [group_name],
        "users": [space_name]
        }
    )

    dozent_scope.append("access:servers!user=" + space_name)
    dozent_scope.append("servers!user=" + space_name)
    dozent_scope.append("read:users!user=" + space_name)
    dozent_scope.append("users:activity!user=" + space_name)
    dozent_scope.append("list:users!user=" + space_name)
    dozent_scope.append("groups!group=" + group_name)
dozent_scope.append("read:users")
dozent_scope.append("admin-ui")
dozent_scope.append("list:users!user")

gruppe_scope = set(gruppe_scope)

c.JupyterHub.load_roles.append(
    {"name": "dozent-role",
     "scopes": dozent_scope,
     "groups": ["dozent"],
    }
)
"""
---------------------------------mount nfs file and create presistant volume for users-----------------------------
"""
def pre_spawn_hook(spawner):
    username = spawner.user.name  # get the username/collaborative account
    volume_path = os.path.join('/srv/ngshare/jupyterhub-user-volumes', username)

    if not os.path.exists(volume_path):
        os.mkdir(volume_path, 0o755)
        os.chown(volume_path,1000,1000)

    mounts = [
              {'type': 'bind',
               'source': '/opt/mount/jupyterhub-user-volumes/' + username,
               'target': '/home/jovyan/work'},

              {'type': 'bind',
               'source': '/opt/mount/DHBW-Kurs/videos',
               'target': '/home/jovyan/work/videos',
               'mode':'ro'},

              {'type': 'bind',
               'source': '/opt/mount/dhbw-user-database/group_info.json',
               'target': '/tmp/exchange/group_info.json',
               'mode':'ro'},

              {'type' : 'bind',
               'target' : '/tmp/exchange',
               'source' : '/opt/mount/DHBW-Kurs'},

              {'type': 'bind',
               'source': '/opt/mount/Project-database',
               'target': '/mnt',
               'mode':'ro'},

              {'type': 'bind',
               'source': '/opt/mount/dhbw-user-database/databrix_user_credential.json',
               'target': '/tmp/exchange/databrix_user_credential.json',
               'mode':'ro'},
              ]

    if 'dozent' in [group.name for group in spawner.user.groups]:
        spawner.extra_container_spec = {'mounts': mounts, "user": "root"}
    else:
        spawner.extra_container_spec = {'mounts': mounts[:-1]}


# attach the hook function to the spawner
c.Spawner.pre_spawn_hook =  pre_spawn_hook
c.Spawner.debug = True
c.DockerSpawner.notebook_dir = '/home/jovyan/work'

"""
-----------------------------------Jupyterhub spawner setting------------------------------------------------------>"""
c.SwarmSpawner.cmd = ["jupyterhub-singleuser", "--allow-root"]
c.SwarmSpawmer.use_internal_ip = True

c.SwarmSpawner.network_name = os.environ["DOCKER_NETWORK_NAME"]
c.SwarmSpawner.extra_host_config = { 'network_mode': os.environ["DOCKER_NETWORK_NAME"] }
c.SwarmSpawner.start_timeout = 500
c.Spawner.http_timeout = 60
c.SwarmSpawner.remove = False
c.Spawner.extra_placement_spec = { "constraints": ["node.role == worker"] }
c.ConfigurableHTTPProxy.debug = True

def image_for_user(user):
    "Given a user, return the right image"
    groups = [group.name for group in user.groups]
    if 'dozent' in groups:
        return "guyq1997/jupyterlab-dhbw:dozent"
    else:
        return "guyq1997/jupyterlab-dhbw:student"
class MySwarmSpawner(SwarmSpawner):
    def start(self):
        self.image = image_for_user(self.user)

        return super().start()

c.JupyterHub.spawner_class = MySwarmSpawner

def default_url(handler):

    user = handler.current_user
    scopes = handler.expanded_scopes
    if not user:
        return handler.get_login_url()

    if len(scopes) == 0:
        return handler.get_login_url()
    else:
        if 'admin-ui' in scopes:
            return '/jupyterhub/hub/admin'
        else:
            scopes = handler.expanded_scopes
            workspace = list(gruppe_scope.intersection(scopes))
            return '/jupyterhub/hub/user/' +workspace[0][13:] + '/lab'
        
c.JupyterHub.default_url =  default_url

#c.JupyterHub.base_url = "/jupyterhub"
c.JupyterHub.hub_ip = "0.0.0.0"
c.JupyterHub.bind_url = 'http://0.0.0.0:8000/jupyterhub'


"""
-----------------------------Setiing and configure shibboleth&clerk Authenticator-----------------------------------"""
c.Authenticator.allow_all = True
c.JupyterHub.shutdown_on_logout = True
c.JupyterHub.authenticator_class = ShibbolethClerkAuthenticator
c.ShibbolethClerkAuthenticator.allow_all = True
c.ShibbolethClerkAuthenticator.client_id = 'xxx'

c.ShibbolethClerkAuthenticator.oauth_callback_url = 'https://xxx.org/jupyterhub/hub/oauth_callback'
c.ShibbolethClerkAuthenticator.authorize_url = "https://tough-lemming-0.clerk.accounts.dev/oauth/authorize"
c.ShibbolethClerkAuthenticator.token_url = "https://tough-lemming-0.clerk.accounts.dev/oauth/token"
c.ShibbolethClerkAuthenticator.userdata_url = "https://tough-lemming-0.clerk.accounts.dev/oauth/userinfo"
c.ShibbolethClerkAuthenticator.scope = ["email", "profile", "public_metadata"]
c.ShibbolethClerkAuthenticator.username_claim = "email"
c.ShibbolethClerkAuthenticator.shibboleth_logout_url = "https://xxx.org/Shibboleth.sso/Logout"
c.JupyterHub.admin_access = True
c.Authenticator.admin_users = {'clerk-yuqiang.gu@dhbw-stuttgart.de'}

# Persist hub data on volume mounted inside container
data_dir = os.environ.get('DATA_VOLUME_CONTAINER', '/data')
c.JupyterHub.cookie_secret_file = os.path.join(data_dir,'jupyterhub_cookie_secret')

c.JupyterHub.db_url = 'postgresql://postgres:{password}@{host}/{db}'.format(
    host=os.environ['POSTGRES_HOST'],
    password=os.environ['POSTGRES_PASSWORD'],
    db=os.environ['POSTGRES_DB'],
)