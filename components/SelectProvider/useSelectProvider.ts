import manifest from "@/data/manifest.json";
import { useState } from "react";

const providerList = Object.entries(manifest.providersOAuth).map(
  ([id, name]) => {
    return { id, name };
  }
);

export function useSelectProvider() {
  const [term, setTerm] = useState("");
  const [selected, setSelected] = useState("");

  function handleSearchItem(term: string) {
    setTerm(term);
  }

  function handleSelectOption(item: { id: string; name: string }) {
    setTerm(item.name);
    setSelected(item.id);
  }

  return {
    items: providerList.filter((item) =>
      item.name.toLowerCase().includes(term.toLowerCase())
    ),
    term,
    selected,
    handleSearchItem,
    handleSelectOption,
  };
}
