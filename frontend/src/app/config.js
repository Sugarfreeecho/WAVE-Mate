/**
 * Runtime config injected by webui.py into index.html before </head>.
 * Defaults allow Vite dev without the Python server for static smoke tests.
 */
export function readRuntimeConfig(root = globalThis) {
  const w = root;
  if (typeof w.__CONTEXT_WINDOW__ !== 'number' || w.__CONTEXT_WINDOW__ <= 0) {
    w.__CONTEXT_WINDOW__ = 128000;
  }
  if (typeof w.__UI_LOG_TRUNCATE_KEEP_LINES__ !== 'number') {
    w.__UI_LOG_TRUNCATE_KEEP_LINES__ = 80;
  }
  if (typeof w.__WORK_DIR__ !== 'string') w.__WORK_DIR__ = '';
  if (typeof w.__SESSIONS_DIR__ !== 'string') w.__SESSIONS_DIR__ = '';
  return {
    contextWindow: w.__CONTEXT_WINDOW__,
    logTruncateKeepLines: w.__UI_LOG_TRUNCATE_KEEP_LINES__,
    workDir: w.__WORK_DIR__,
    sessionsDir: w.__SESSIONS_DIR__,
  };
}
