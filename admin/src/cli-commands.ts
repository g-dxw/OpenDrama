/** Keep copied setup commands bound to the instance that issued the key. */
const shellQuote = (value = "") =>
  "'" + value.replace(/'/g, "'\"'\"'") + "'";

export function cliCommands(origin = "", apiBase = "") {
  const installer = `curl -fsSL ${shellQuote(origin + "/cli/install.sh")} | OPENDRAMA_CLI_BASE=${shellQuote(origin + "/cli")} OPENDRAMA_API_BASE=${shellQuote(apiBase || origin)}`;
  return {
    install: `${installer} sh`,
    setKey: `opendrama config set-key --base-url ${shellQuote(apiBase || origin)}`,
    doctor: "opendrama doctor",
    oneLiner: (key = "") => `${installer} OPENDRAMA_API_KEY=${shellQuote(key)} sh`,
  };
}
