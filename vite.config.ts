import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "static/dist",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: {
        app: "frontend/src/app.ts",
        aiTasks: "frontend/src/ai-tasks.ts"
      }
    }
  }
});
