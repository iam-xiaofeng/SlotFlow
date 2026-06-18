import { type ChatUiMessage } from "@/hooks/use-chat-stream";
import {
  type ModelCatalogRecord,
  type ModelOptionRecord,
  type UploadedFileRecord,
} from "@/lib/chat-stream";

export const defaultModelName = "deepseek-v4-pro";

export type ThreadArtifactIndex = Record<string, string[]>;

const threadArtifactStorageKey = "slotflow.thread-artifacts.v1";

export function extractFileIdsFromMessage(message: ChatUiMessage | undefined): string[] {
  const files = message?.metadata?.files;
  if (Array.isArray(files)) {
    return files.filter((fileId): fileId is string => typeof fileId === "string");
  }

  return extractUploadedFilesFromMetadata(message?.metadata).flatMap((item) => {
    if (
      typeof item === "object" &&
      item !== null &&
      "id" in item &&
      typeof item.id === "string"
    ) {
      return [item.id];
    }
    return [];
  });
}

export function extractUploadedFilesFromMetadata(
  metadata: Record<string, unknown> | undefined,
): unknown[] {
  const uploadedFiles = metadata?.uploaded_files;
  return Array.isArray(uploadedFiles) ? uploadedFiles : [];
}

export function makeQueueId() {
  return `queued_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export function flattenModelOptions(catalog: ModelCatalogRecord | null): ModelOptionRecord[] {
  if (!catalog) {
    return [
      {
        id: defaultModelName,
        provider: "deepseek",
        label: `DeepSeek · ${defaultModelName}`,
        available: true,
        source: "fallback",
      },
    ];
  }

  return catalog.providers.flatMap((provider) =>
    provider.models.filter((model) => model.available),
  );
}

export function modelExists(catalog: ModelCatalogRecord, modelName: string): boolean {
  return catalog.providers.some((provider) =>
    provider.models.some((model) => model.available && model.id === modelName),
  );
}

export function formatUploadToast(files: UploadedFileRecord[]) {
  if (files.length === 1) {
    return `${files[0].original_filename ?? files[0].filename} uploaded`;
  }
  return `${files.length} files uploaded`;
}

export function sortRecordsByNames<T extends { name: string }>(records: T[], names: string[]): T[] {
  const position = new Map(names.map((name, index) => [name, index]));
  return [...records].sort(
    (left, right) =>
      (position.get(left.name) ?? records.length) -
      (position.get(right.name) ?? records.length),
  );
}

export function removeArtifactPathFromThreadIndex(
  index: ThreadArtifactIndex,
  path: string,
): ThreadArtifactIndex {
  let changed = false;
  const nextEntries = Object.entries(index).flatMap(([threadId, paths]) => {
    const nextPaths = paths.filter((item) => item !== path);
    if (nextPaths.length !== paths.length) {
      changed = true;
    }
    return nextPaths.length > 0 ? [[threadId, nextPaths] as const] : [];
  });
  return changed ? Object.fromEntries(nextEntries) : index;
}

export function readThreadArtifactIndex(): ThreadArtifactIndex {
  if (typeof window === "undefined") {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(threadArtifactStorageKey);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).flatMap(([threadId, paths]) => {
        if (!Array.isArray(paths)) {
          return [];
        }
        return [
          [
            threadId,
            paths.filter((path): path is string => typeof path === "string" && path.length > 0),
          ],
        ];
      }),
    );
  } catch {
    return {};
  }
}

export function writeThreadArtifactIndex(index: ThreadArtifactIndex) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(threadArtifactStorageKey, JSON.stringify(index));
}
