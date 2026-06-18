"use client";

import { useCallback, useState } from "react";

import {
  type McpServerRecord,
  type MemoryRecord,
  type SkillRecord,
  type WorkspaceEntryRecord,
  listArtifacts,
  listMcpServers,
  listMemories,
  listSkills,
} from "@/lib/chat-stream";

/** Owns the workspace context data (artifacts / skills / MCP / memories) and their refreshers. */
export function useWorkspaceData() {
  const [artifacts, setArtifacts] = useState<WorkspaceEntryRecord[]>([]);
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerRecord[]>([]);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);

  const refreshArtifacts = useCallback(async () => {
    try {
      const nextArtifacts = await listArtifacts();
      setArtifacts(nextArtifacts);
      return nextArtifacts;
    } catch {
      setArtifacts([]);
      return [];
    }
  }, []);

  const refreshSkills = useCallback(async () => {
    try {
      setSkills(await listSkills());
    } catch {
      setSkills([]);
    }
  }, []);

  const refreshMcpServers = useCallback(async () => {
    try {
      setMcpServers(await listMcpServers());
    } catch {
      setMcpServers([]);
    }
  }, []);

  const refreshMemories = useCallback(async () => {
    try {
      setMemories(await listMemories());
    } catch {
      setMemories([]);
    }
  }, []);

  return {
    artifacts,
    setArtifacts,
    skills,
    setSkills,
    mcpServers,
    setMcpServers,
    memories,
    setMemories,
    refreshArtifacts,
    refreshSkills,
    refreshMcpServers,
    refreshMemories,
  };
}
