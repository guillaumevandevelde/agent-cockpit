// Vitest setup: registers custom jest-dom matchers (`toBeInTheDocument`,
// `toHaveTextContent`, etc.) so they extend Vitest's `expect` globally.
// Wired via `test.setupFiles` in `vite.config.ts`. See kanban card
// 84b7bc21981f40a6ab31d5a118a733aa for context.
import "@testing-library/jest-dom/vitest";
