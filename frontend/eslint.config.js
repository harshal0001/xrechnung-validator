// Flat config. The rule sets are the recommended ones and nothing bespoke:
// the point of running a linter in CI is the class of mistake TypeScript does
// not catch — a hook called conditionally, a variable assigned and never read —
// not a house style.
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  { ignores: ["dist/", "node_modules/", "scripts/"] },
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: reactHooks.configs.recommended.rules,
  },
);
