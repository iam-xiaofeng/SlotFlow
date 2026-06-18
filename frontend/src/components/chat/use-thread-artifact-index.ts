"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type ThreadArtifactIndex,
  readThreadArtifactIndex,
  writeThreadArtifactIndex,
} from "./chat-app-helpers";

/** Owns the per-thread artifact-path index, hydrated from and persisted to localStorage. */
export function useThreadArtifactIndex() {
  const [threadArtifactPaths, setThreadArtifactPaths] = useState<ThreadArtifactIndex>({});
  const hasLoadedRef = useRef(false);

  useEffect(() => {
    setThreadArtifactPaths(readThreadArtifactIndex());
    hasLoadedRef.current = true;
  }, []);

  useEffect(() => {
    if (!hasLoadedRef.current) {
      return;
    }
    writeThreadArtifactIndex(threadArtifactPaths);
  }, [threadArtifactPaths]);

  const rememberThreadArtifacts = useCallback((threadId: string, paths: string[]) => {
    if (paths.length === 0) {
      return;
    }
    setThreadArtifactPaths((current) => {
      const existing = current[threadId] ?? [];
      const merged = [...existing];
      for (const path of paths) {
        if (!merged.includes(path)) {
          merged.push(path);
        }
      }
      return {
        ...current,
        [threadId]: merged,
      };
    });
  }, []);

  return { threadArtifactPaths, setThreadArtifactPaths, rememberThreadArtifacts };
}
