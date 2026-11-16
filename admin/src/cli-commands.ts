/** Keep copied setup commands bound to the instance that issued the key. */
const shellQuote = (value = "") =>
  "'" + value.replace(/'/g, "'\"'\"'") + "'";

export function cliCommands(origin = "", apiBase = "") {
  const installer = `curl -fsSL ${shellQuote(origin + "/cli/install.sh")} | MINITV_CLI_BASE=${shellQuote(origin + "/cli")} MINITV_API_BASE=${shellQuote(apiBase || origin)}`;
  return {
    install: `${installer} sh`,
    setKey: `minitv config set-key --base-url ${shellQuote(apiBase || origin)}`,
    doctor: "minitv doctor",
    oneLiner: (key = "") => `${installer} MINITV_API_KEY=${shellQuote(key)} sh`,
  };
}
