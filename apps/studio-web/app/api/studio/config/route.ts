import { NextResponse } from 'next/server';

import { getAllowedRepoRoots, getDefaultRepoRoot } from '@/lib/studio/paths';

export const runtime = 'nodejs';

export async function GET() {
  const allowedRepoRoots = getAllowedRepoRoots();
  const defaultRepoRoot = process.env.NOCTUNE_STUDIO_DEFAULT_REPO_ROOT
    ? process.env.NOCTUNE_STUDIO_DEFAULT_REPO_ROOT
    : getDefaultRepoRoot();

  return NextResponse.json({
    ok: true,
    allowedRepoRoots,
    defaultRepoRoot,
    llm: {
      baseUrl:
        process.env.NOCTUNE_STUDIO_LLM_BASE_URL ?? 'http://127.0.0.1:8080/v1',
      model: process.env.NOCTUNE_STUDIO_LLM_MODEL ?? '',
      hasApiKey: Boolean(process.env.NOCTUNE_STUDIO_LLM_API_KEY),
    },
  });
}

