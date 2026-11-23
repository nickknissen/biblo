# MCP server (example)

An [MCP](https://modelcontextprotocol.io/) server built on this library. It lets an AI
assistant such as Claude search your public library and read your own account: "what do I
have due this week?", "find Danish audiobooks about vikings", "is my reservation ready?".

All tools only read. Borrowing, renewing and reserving stay with you, in the Biblo app,
on the library's website or with the `biblo` CLI.

## Tools

| Tool | Needs login | What it returns |
| --- | --- | --- |
| `search_catalogue` | no | works matching a query, optionally only e-books, audiobooks, podcasts or magazines, from the digital, children's or whole catalogue |
| `get_work` | no | description, subjects, series, editions, and per format whether it can be borrowed now or reserved |
| `find_library` | no | library ids by name or city |
| `my_loans` | yes | loans, soonest due first, overdue and renewable flags |
| `my_reservations` | yes | reservations: ready for pickup and by when, queue position, pickup branch |
| `my_favorites` | yes | the saved list ("Huskeliste") |
| `my_account` | yes | e-book and audiobook loans left this period, preferred pickup branch, fees |
| `show_my_library` | yes | loans, reservations and digital loans left, shown as an interactive view (below) |

`my_account` leaves out the name, email and phone number on the account.

## The "My library" view (MCP App)

`show_my_library` is also an [MCP App](https://modelcontextprotocol.io/extensions/apps/overview):
in a host that supports MCP Apps, its result is shown as a view in the conversation, not
only as text. The view has:

- counts of loans, overdue loans, reservations ready for pickup, and e-book and audiobook
  loans left;
- reservations, ready ones first, with the pickup deadline and branch, or the queue
  position;
- loans, soonest due first, overdue and due within three days marked;
- covers, and titles that open the work on bibliotek.dk;
- a Refresh button, which calls the tool again.

It follows the host's light or dark theme and its style variables, and only reads.
Clients without MCP Apps get the same data as JSON.

The view is [`my_library.html`](my_library.html): one HTML file, plain CSS and JavaScript,
no build step. It speaks the MCP Apps view protocol (2026-01-26) directly over
`postMessage`. The server registers it with the SDK's `mcp.server.apps.Apps` extension,
and allows images from `fbiinfo-present.dbc.dk`, where the covers come from; the view's
sandbox blocks every other host.

## Set up

1. Log in once with the CLI. The server uses the session the CLI saves, so your card
   number and pincode never go into the MCP client's config:

   ```sh
   uvx --from "biblo[cli]==0.2.0" biblo auth login
   ```

   Without a login the catalogue tools still work. Set `BIBLO_LIBRARY` to a library id
   (e.g. `763000`); it also picks the catalogue's library when you are logged in.

2. Add the server to your MCP client. For Claude Code:

   ```sh
   claude mcp add biblo -- uv run --with "biblo[cli]==0.2.0" --with "mcp==2.3.0" \
       /path/to/biblo/examples/mcp_server/biblo_mcp.py
   ```

   For Claude Desktop, in `claude_desktop_config.json`:

   ```json
   {
     "mcpServers": {
       "biblo": {
         "command": "uv",
         "args": [
           "run", "--with", "biblo[cli]==0.2.0", "--with", "mcp==2.3.0",
           "/path/to/biblo/examples/mcp_server/biblo_mcp.py"
         ]
       }
     }
   }
   ```

The session file is the CLI's: in `$BIBLO_CONFIG_DIR` if set, else the platform's config
folder (`~/.config/biblo` on Linux). The server refreshes its tokens there, like the CLI.
Requests to the API say they come from `biblo-mcp` in their User-Agent.

## Tests

The tests call every tool through an in-process MCP client, against the fake Biblo API in
this repo's `tests/fake.py`:

```sh
uv run --with "biblo[cli]==0.2.0" --with "mcp==2.3.0" --with pytest --with pytest-asyncio \
    pytest examples/mcp_server -o addopts=""
```

They also check that `show_my_library` links its view, that the view is served as
`text/html;profile=mcp-app` with the cover host allowed, and what data it gets.

Written for the MCP Python SDK 2.x (`MCPServer`; 1.x called it `FastMCP`). Tested with
`mcp` 2.3.0:

- the tests;
- the server over stdio against the live API without a login (protocol version
  2026-07-28);
- the view in Chromium inside a stand-in host that speaks the MCP Apps protocol: the
  handshake, rendering in light and dark, its height, Refresh and opening links.

It hasn't been shown in a real MCP Apps host, such as Claude, or run against a real
account yet.
