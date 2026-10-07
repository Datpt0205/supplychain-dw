---
status: Accepted
date: 2026-10-07
source:
    - ../../packages/python/dw_connectors/src/dw_connectors/ports.py # ChatSenderPort
    - ../../packages/python/dw_connectors/src/dw_connectors/adapters/zalo_bot.py # ZaloBotClient, _split_for_zalo
    - ../../packages/python/dw_connectors/src/dw_connectors/adapters/zalo_link.py # link token, handle_update
    - ../../packages/python/dw_connectors/src/dw_connectors/adapters/zalo_inbound.py # ZaloInbound
    - ../../packages/python/dw_connectors/src/dw_connectors/inbound.py # InboundRouter, ChannelCommandRegistry
    - ../../packages/python/dw_platform/src/dw_platform/adapters/persistence/zalo_link_repo.py # SqlZaloLink
    - ../../packages/python/dw_platform/src/dw_platform/adapters/persistence/membership_lookup.py # find_linked_access
    - ../../packages/python/dw_platform/src/dw_platform/application/channel_access.py # LinkedUserAccess
    - ../../db/migrations/versions/02930a73bbdf_platform_channel_link_nonces.py
    - ../../db/migrations/versions/9f2becb1bf80_platform_channel_inbound_messages_and_.py
---

# 0005. The Zalo bot is the first chat channel; a link is never a login, and an inbound command acts with a context built server-side from the linked person's membership

Accepted in the first product built on the platform (its ADR 0012, 2026-10-05,
amended 2026-10-07), and upstreamed from there without its product commands.

## Context

The platform reached people only through the web inbox. The first product's
users work in Zalo, not Slack or email, and asked for the bot to be a two-way
work channel: notifications out, a few commands in. `dw_connectors` already
had a Zalo Bot Platform client and a link flow, unwired and with a reusable
link token.

## Decision

**One bot per deployment** (`ZALO_BOT_TOKEN`). A bot per tenant needs a link
table that carries `tenant_id`; not built.

1. **A person links their own chat.** Signed in, they get a one-time
   `/start <token>` (`POST /api/v1/zalo/connect`) and send it to the bot. The chat
   id is stored in `platform.external_identities` (`issuer = provider = 'zalo'`)
   through `SqlZaloLink`. `/stop` in the chat or `POST /api/v1/zalo/disconnect`
   removes it. Only those two commands touch the link; no other word does.
2. **The token is single-use.** It carries a `jti` written to
   `platform.channel_link_nonces` when issued and consumed by one conditional
   UPDATE in the transaction that writes the link (never read-then-update). A
   lane deletes rows a day past expiry. Linking, relinking and unlinking each
   write an audit row and an in-app notice in every tenant the person belongs
   to, in the same transaction, so a token used by someone looking over a
   shoulder is visible to its owner.
3. **A link is not a login identity.** `SqlMembershipLookup.find_access`
   refuses a `zalo` identity and `SqlIdentityBootstrap` never resolves one; the
   JWT path never sees a chat. Tests assert both.
4. **An inbound command builds its context on a separate, server-side path**
   (`InboundRouter`):
    - the chat id resolves to a `user_id` through the link; an unlinked chat
      gets one link sentence and reaches no command;
    - the message id is claimed in `platform.channel_inbound_messages` and
      committed before anything acts, so a redelivered update acts once;
    - the workspace is the person's only membership, or the one they chose
      (`PUT /api/v1/zalo/workspace`, `platform.channel_preferences`); several and
      no choice gets a link to the settings page, never a question in chat;
    - the context is built by `find_linked_access` from the membership looked up
      by `user_id`: scopes are the membership's effective scopes intersected with
      the command's declared ceiling, and **no role** is carried, because
      `platform_admin` passes every scope check and would break the ceiling;
    - nothing in the message chooses a tenant, workspace, person or scope.
5. **Commands are registered at the composition root**
   (`ChannelCommandRegistry`), each with its ceiling. The platform ships the
   registry empty, and empty is a working state ("Mình chưa xử lý được tin
   này"). A product adds its commands there.
6. **Runs as `dw_app`.** Every store here uses the request pool; none uses the
   provisioner.
7. **The product name is injected** (`DW_PRODUCT_NAME`) into the bot's replies.
8. **Channel properties live in the adapter:** messages are split at 1900
   characters (Zalo silently refuses more than 2000), and the bot token, which
   rides in every Bot API URL, is scrubbed from errors and logs.
9. **Import contracts:** the link flow, bot client and poll lane cannot reach
   `dw_agent_runtime`, `dw_platform` or `dw_api`; the `/zalo` routes take neither
   the approval service nor the access-context factory.

## Alternatives considered

- **Zalo OA with ZNS.** Reaches people who never wrote to the bot, but needs a
  verified business account, approved templates and a fee per message. Not
  needed for staff.
- **A Zalo id map in `.env` or yaml.** A second copy of the user list.

## Consequences

- The Bot Platform can only message people who wrote to the bot first, so this
  channel is for staff, not suppliers or customers.
- One link serves every tenant the person belongs to; outgoing messages name
  the tenant and workspace.
- Between a stranger linking a seen token and the owner reading the notice,
  the stranger's chat can run whatever commands the owner's ceiling allows.
  Free text decides no approval: the link flow cannot reach the approval
  service (import contract above).

## Amendment 2026-10-07: a model call in the worker goes through the shared builder

Accepted in the first product (its ADR 0012 amendment for Z4b) and upstreamed
here. A chat command that reads a message with a model runs in the worker,
which had no `ModelGateway`.

- **One builder, two composition roots.** `dw_agent_runtime.adapters.model_stack`
  (`build_model_stack`, `ModelStack`) builds the provider adapters (SSRF guard,
  no mock in a deployed profile), one `RunBudgetLedger` per process and the
  usage recorders (daily spend guard, telemetry). The API
  (`bootstrap/runtime.py`) and the worker
  (`dw_worker.composition.build_model_stack_for`) both call it; each maps only
  its own settings onto `ModelProviderConfig`. `apps/worker` does not import
  `dw_api`.
- **A one-call gateway checks the plan.** `DailyAllowance`
  (`dw_agent_runtime.allowance`) is the check the runner made at a run's start
  (runs per day, spend per day), extracted so the runner and
  `SingleCallModelGateway` share it. `ModelStack.one_call(allowance)` checks it
  before every call no run surrounds, then frees that call's ledger entry.
