import { createOpenAI } from '@ai-sdk/openai';
import {
  convertToModelMessages,
  createUIMessageStream,
  JsonToSseTransformStream,
  smoothStream,
  streamText,
  tool,
} from 'ai';
import { z } from 'zod';
import { spawn } from 'node:child_process';

import type { UIMessage } from 'ai';
import { computeAllowWrite, isSafeCachePath } from '@/lib/studio/permissions';
import { resolveRepoRootOrThrow } from '@/lib/studio/paths';
import { readTextFile, replaceLines, writeTextFile } from '@/lib/studio/repo_fs';
import {
  decideApproval,
  listPendingApprovals,
  startNoctuneRun,
  stopNoctuneRun,
  tailNoctuneEvents,
} from '@/lib/studio/noctune';

export const runtime = 'nodejs';
export const maxDuration = 60;

type StudioMessage = UIMessage<any, any, any>;

export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as
    | {
        repoRoot?: string;
        model?: string;
        system?: string;
        allowToken?: string | null;
        messages?: StudioMessage[];
      }
    | null;

  const repoRootRaw = body?.repoRoot ? String(body.repoRoot) : '';
  if (!repoRootRaw) {
    return new Response(JSON.stringify({ ok: false, error: 'repoRoot is required' }), {
      status: 400,
      headers: { 'content-type': 'application/json' },
    });
  }
  const repoRoot = resolveRepoRootOrThrow(repoRootRaw);

  const messages = Array.isArray(body?.messages) ? body?.messages : [];
  if (!messages.length) {
    return new Response(JSON.stringify({ ok: false, error: 'messages is required' }), {
      status: 400,
      headers: { 'content-type': 'application/json' },
    });
  }

  const allow = await computeAllowWrite({
    repoRoot,
    allowToken: body?.allowToken ?? null,
    cookieHeader: request.headers.get('cookie'),
  });

  const baseUrl =
    process.env.NOCTUNE_STUDIO_LLM_BASE_URL ?? 'http://127.0.0.1:8080/v1';
  const apiKey = process.env.NOCTUNE_STUDIO_LLM_API_KEY ?? 'local';
  const model =
    (body?.model && String(body.model).trim()) ||
    (process.env.NOCTUNE_STUDIO_LLM_MODEL ?? '').trim();

  if (!model) {
    return new Response(
      JSON.stringify({
        ok: false,
        error: 'Missing model. Set NOCTUNE_STUDIO_LLM_MODEL or pass { model }.',
      }),
      { status: 400, headers: { 'content-type': 'application/json' } },
    );
  }

  const openai = createOpenAI({ apiKey, baseURL: baseUrl });

  const system =
    (body?.system && String(body.system)) ||
    `You are Noctune Studio, a local-first coding assistant.\n\nRepo root: ${repoRoot}\n\nIf a write/run tool is needed and permission is not granted, ask the user to click one of:\n"Allow once", "Allow this session", or "Always allow".\nCurrent permission: ${allow.allowed ? `ALLOWED (${allow.mode})` : 'NOT ALLOWED'}`;

  const readFileTool = tool({
    description: 'Read a UTF-8 text file from repoRoot (path is repo-relative).',
    inputSchema: z.object({
      path: z.string(),
      maxBytes: z.number().optional(),
    }),
    execute: async ({ path: p, maxBytes }) => {
      return readTextFile(repoRoot, p, { maxBytes });
    },
  });

  const searchTool = tool({
    description:
      'Search text in repoRoot using ripgrep (rg). Returns up to ~200 lines.',
    inputSchema: z.object({
      query: z.string(),
      globs: z.array(z.string()).optional(),
    }),
    execute: async ({ query, globs }) => {
      const args = ['-n', '--hidden', '--glob', '!.git', query, repoRoot];
      if (Array.isArray(globs)) {
        for (const g of globs) {
          args.splice(2, 0, '--glob', g);
        }
      }
      try {
        const child = spawn('rg', args, { cwd: repoRoot });
        let out = '';
        let err = '';
        child.stdout.on('data', (d) => (out += String(d)));
        child.stderr.on('data', (d) => (err += String(d)));
        const code: number = await new Promise((resolve) => {
          child.on('close', (c) => resolve(c ?? 0));
          child.on('error', () => resolve(127));
        });
        const text = (out || err || '').split('\n').slice(0, 200).join('\n');
        return { ok: code === 0 || code === 1, exitCode: code, output: text };
      } catch (e: any) {
        return { ok: false, exitCode: 127, output: `rg failed: ${String(e?.message ?? e)}` };
      }
    },
  });

  const writeFileTool = tool({
    description:
      'Write/overwrite a UTF-8 text file under repoRoot. Requires permission unless writing into .noctune_cache/.',
    inputSchema: z.object({
      path: z.string(),
      content: z.string(),
    }),
    execute: async ({ path: p, content }) => {
      if (!allow.allowed && !isSafeCachePath(repoRoot, p)) {
        return { ok: false, error: 'write_not_allowed' };
      }
      const res = await writeTextFile(repoRoot, p, content);
      return { ok: true, ...res };
    },
  });

  const replaceLinesTool = tool({
    description:
      'Replace an inclusive line range (1-based) in a file. Requires permission unless writing into .noctune_cache/.',
    inputSchema: z.object({
      path: z.string(),
      startLine: z.number(),
      endLine: z.number(),
      replacement: z.string(),
    }),
    execute: async ({ path: p, startLine, endLine, replacement }) => {
      if (!allow.allowed && !isSafeCachePath(repoRoot, p)) {
        return { ok: false, error: 'write_not_allowed' };
      }
      const res = await replaceLines(repoRoot, p, startLine, endLine, replacement);
      return { ok: true, ...res };
    },
  });

  const noctuneStartTool = tool({
    description: 'Start a Noctune job (review|edit|repair|run). Requires permission.',
    inputSchema: z.object({
      stage: z.enum(['review', 'edit', 'repair', 'run']),
      relPaths: z.array(z.string()).optional(),
      extraArgs: z.array(z.string()).optional(),
    }),
    execute: async ({ stage, relPaths, extraArgs }) => {
      if (!allow.allowed) return { ok: false, error: 'run_not_allowed' };
      const h = await startNoctuneRun({
        repoRoot,
        stage,
        relPaths: relPaths ?? null,
        extraArgs: extraArgs ?? null,
        runId: null,
      });
      return { ok: true, ...h };
    },
  });

  const noctuneStopTool = tool({
    description: 'Stop a Noctune run (stop.flag + optional SIGTERM). Requires permission.',
    inputSchema: z.object({
      runId: z.string(),
      pid: z.number().optional(),
    }),
    execute: async ({ runId, pid }) => {
      if (!allow.allowed) return { ok: false, error: 'run_not_allowed' };
      const r = await stopNoctuneRun({ repoRoot, runId, pid: pid ?? null });
      return { ok: true, ...r };
    },
  });

  const noctuneEventsTool = tool({
    description: 'Tail Noctune events.jsonl for a run.',
    inputSchema: z.object({
      runId: z.string(),
      cursor: z.number().optional(),
      limit: z.number().optional(),
    }),
    execute: async ({ runId, cursor, limit }) => {
      return tailNoctuneEvents({
        repoRoot,
        runId,
        cursor: cursor ?? null,
        limit: limit ?? null,
      });
    },
  });

  const noctuneApprovalsTool = tool({
    description: 'List pending approvals for a Noctune run.',
    inputSchema: z.object({
      runId: z.string(),
    }),
    execute: async ({ runId }) => {
      return listPendingApprovals({ repoRoot, runId });
    },
  });

  const noctuneDecideTool = tool({
    description: 'Approve/reject a Noctune approval. Requires permission.',
    inputSchema: z.object({
      runId: z.string(),
      approvalId: z.string(),
      approved: z.boolean(),
      reason: z.string().optional(),
    }),
    execute: async ({ runId, approvalId, approved, reason }) => {
      if (!allow.allowed) return { ok: false, error: 'run_not_allowed' };
      return decideApproval({
        repoRoot,
        runId,
        approvalId,
        approved,
        reason: reason ?? '',
      });
    },
  });

  const stream = createUIMessageStream({
    execute: ({ writer: dataStream }) => {
      const result = streamText({
        model: openai.chat(model),
        system,
        messages: convertToModelMessages(messages),
        tools: {
          readFile: readFileTool,
          search: searchTool,
          writeFile: writeFileTool,
          replaceLines: replaceLinesTool,
          noctuneStart: noctuneStartTool,
          noctuneStop: noctuneStopTool,
          noctuneEvents: noctuneEventsTool,
          noctuneApprovals: noctuneApprovalsTool,
          noctuneDecide: noctuneDecideTool,
        },
        experimental_transform: smoothStream({ chunking: 'word' }),
      });

      result.consumeStream();
      dataStream.merge(result.toUIMessageStream({ sendReasoning: true }));
    },
    onError: () => 'Oops, an error occurred.',
  });

  return new Response(stream.pipeThrough(new JsonToSseTransformStream()));
}
