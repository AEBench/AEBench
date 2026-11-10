const fs = require("node:fs");
const key = process.env.ZAI_CODING_PLAN_KEY;
if (!key || /\s/.test(key)) throw new Error("Coding Plan key missing or malformed");
const config = {
  schemaVersion: 1,
  config: {
    providerConfigRules: {
      providerRules: [{
        providerId: "zai-coding",
        providerName: "Z.AI Coding Plan",
        config: {
          group: "standard-personal",
          access: { type: "zhipu-coding-plan-api-key", apiKey: key },
          api: { type: "anthropic-messages", baseUrl: "https://api.z.ai/api/anthropic" },
          personalModelIds: ["glm-5.3-flash"],
        },
      }],
    },
    modelConfigRules: { providerModelRules: [], manualProviderModelRules: [] },
    defaultModelSelection: {
      providerId: "zai-coding", modelId: "glm-5.3-flash", options: { reasoningLevel: "high" },
    },
  },
};
fs.writeFileSync(process.env.ZCODE_PERSONAL_PROVIDER_CONFIG_FILE, JSON.stringify(config), {
  mode: 0o600, flag: "wx",
});
