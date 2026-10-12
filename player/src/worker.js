/** Normal module worker, with the existing opaque-origin iframe bootstrap. */
export function createModuleWorker(url) {
  try {
    return new Worker(url, { type: "module" });
  } catch (error) {
    if (error.name !== "SecurityError") throw error;
  }
  const script = `import ${JSON.stringify(url.href)};`;
  return new Worker(`data:text/javascript,${encodeURIComponent(script)}`, { type: "module" });
}
