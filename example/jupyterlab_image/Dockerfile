FROM jupyter/base-notebook


ARG NB_USER
USER ${NB_USER}

# Copy environment files
COPY requirements_dhbw-kurs.txt /opt/requirements_dhbw-kurs.txt
#COPY Assignment /home/jovyan/Assignment
COPY info-all.json /efs/user-info/info-all.json
COPY nbgrader_config.py /etc/jupyter/nbgrader_config.py
COPY nbgrader_directory.py /home/jovyan/.jupyter/nbgrader_config.py


USER root
RUN chmod ugo+rw /etc/jupyter
RUN rm -rf /tmp/exchange
RUN mkdir /tmp/exchange

# Set the default user
ARG NB_USER
USER ${NB_USER}
# Install systemwide JupyterLab extension
RUN pip install 'jupyter-collaboration==2.0.11' 'nbgrader==0.9.2' \
    && pip install matplotlib ipympl plotly virtualenv jupyterlab_logout \
    && pip install 'jupyterlab==4.1.6' jupyterlab-lsp 'jlab_homepage==0.1.3'

RUN virtualenv /opt/conda/share/jupyter/lab/dhbw-kurs \
    && source /opt/conda/share/jupyter/lab/dhbw-kurs/bin/activate \
    && pip install -r /opt/requirements_dhbw-kurs.txt \
    && python -m ipykernel install --user --name=DHBW-Kurs
# Activate Virtual environments and install IPython kernels

RUN jupyter labextension enable nbgrader:create-assignment \
    && jupyter labextension lock nbgrader:create-assignment \
    && jupyter labextension enable nbgrader:formgrader \
    && jupyter labextension lock nbgrader:formgrader \
    && jupyter labextension enable nbgrader:course-list \
    && jupyter labextension lock nbgrader:course-list \
    && jupyter labextension enable @jupyterlab/launcher-extension \
    && jupyter labextension lock @jupyterlab/launcher-extension \
    && jupyter labextension enable @jupyterlab/apputils-extension:announcements \
    && jupyter labextension lock @jupyterlab/apputils-extension:announcements \
    && jupyter lab build