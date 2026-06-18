"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  type ModelCatalogRecord,
  type ModelOptionRecord,
  listChatModels,
} from "@/lib/chat-stream";

import { defaultModelName, flattenModelOptions, modelExists } from "./chat-app-helpers";

/** Owns the model catalog fetch + the currently selected model id. */
export function useModelCatalog() {
  const [modelCatalog, setModelCatalog] = useState<ModelCatalogRecord | null>(null);
  const [selectedModelName, setSelectedModelName] = useState(defaultModelName);
  const [isLoadingModels, setIsLoadingModels] = useState(true);

  useEffect(() => {
    let active = true;

    async function refreshModelCatalog() {
      setIsLoadingModels(true);
      try {
        const catalog = await listChatModels();
        if (!active) {
          return;
        }
        setModelCatalog(catalog);
        setSelectedModelName((current) =>
          modelExists(catalog, current) ? current : catalog.default_model,
        );
      } catch (caught) {
        const message = caught instanceof Error ? caught.message : "load models failed";
        toast.error(message);
        if (active) {
          setModelCatalog(null);
        }
      } finally {
        if (active) {
          setIsLoadingModels(false);
        }
      }
    }

    void refreshModelCatalog();

    return () => {
      active = false;
    };
  }, []);

  const modelOptions: ModelOptionRecord[] = useMemo(
    () => flattenModelOptions(modelCatalog),
    [modelCatalog],
  );

  return { isLoadingModels, selectedModelName, setSelectedModelName, modelOptions };
}
