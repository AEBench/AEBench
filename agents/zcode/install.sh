#!/bin/bash
set -euo pipefail

# Build the same CLI source used for the GLM collection, with a private Node.
prefix="$AEBENCH_AGENT_PREFIX"
npm install --prefix "$prefix" --no-audit --no-fund --no-save \
    node@24.14.0 pnpm@10.33.2 playwright-core@1.59.1 koffi@2.15.6
export PATH="$prefix/node_modules/.bin:$PATH"
source_dir="$(mktemp -d)"
trap 'rm -rf -- "$source_dir"' EXIT
git -C "$source_dir" init -q
git -C "$source_dir" fetch --depth 1 https://github.com/zai-org/ZCode.git \
    a60ba6a7e5a06742270ca605f5d2cc2766afa7e1
git -C "$source_dir" checkout --detach FETCH_HEAD
(
    cd "$source_dir"
    pnpm install --frozen-lockfile
    pnpm --filter @zcode/cli... build
    NATIVE_SEARCH_TOOLS_ARTIFACT_SOURCE=repository \
        node scripts/prepare-native-search-tools.mjs --output-dir "$prefix/tools"
)
cp -a "$source_dir/apps/zcode-cli/packages/cli/dist" "$prefix/cli"
printf '#!/bin/bash\nexport ZCODE_BFS_BINARY=%q ZCODE_RG_BINARY=%q ZCODE_UGREP_BINARY=%q\nexec %q %q "$@"\n' \
    "$prefix/tools/bfs/bfs" "$prefix/tools/ripgrep/rg" "$prefix/tools/ugrep/ugrep" \
    "$prefix/node_modules/.bin/node" "$prefix/cli/zcode.cjs" > "$prefix/zcode"
chmod -R a+rX "$prefix"
chmod 0755 "$prefix/zcode"
ln -s "$prefix/zcode" /usr/local/bin/zcode
zcode --version
zcode doctor --json
