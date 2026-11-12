// Generates TypeScript types from the contract schemas (../contract/schemas), so the dashboard and the engine cannot drift apart:
// a field renamed in Python changes the schema, which changes these types, which fails the dashboard's type check in CI.
import { compile } from "json-schema-to-typescript";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const schemas = join(here, "..", "..", "contract", "schemas");
const out = join(here, "..", "src", "generated");
mkdirSync(out, { recursive: true });

for (const name of ["live", "manifest", "ledger-trade", "swing"]) {
  const schema = JSON.parse(readFileSync(join(schemas, `${name}.schema.json`), "utf8"));
  const ts = await compile(schema, schema.title, {
    bannerComment: `/* Generated from contract/schemas/${name}.schema.json by scripts/gen-types.mjs. Do not edit. */`,
    additionalProperties: false,
    style: { singleQuote: false, semi: true },
  });
  writeFileSync(join(out, `${name}.ts`), ts);
}
console.log("contract types generated");
