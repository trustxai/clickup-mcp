# Changelog

## 0.1.0 (2026-07-08)


### Features

* **chat-channels:** implement Chat v3 channel tools [t16-chat-channels] ([c6a8fac](https://github.com/trustxai/clickup-mcp/commit/c6a8face46b82b53aefd6e4c5a117aeebd57d7c4))
* **chat-messages:** add Chat v3 messages tool module [t17-chat-messages] ([a946245](https://github.com/trustxai/clickup-mcp/commit/a9462450b133e0e74c73fd0289ec338fcd52b435))
* **checklists,tags:** implement checklist CRUD and space-tag tools [t8-checklists-tags] ([c7942e3](https://github.com/trustxai/clickup-mcp/commit/c7942e364f7af9355cfac36e9dbedb0280ea51c8))
* **comments:** implement task/list/chat-view comments tool module [t7-comments] ([f982990](https://github.com/trustxai/clickup-mcp/commit/f9829901db6572efc06ffb649d643cc7af7b3047))
* **custom-fields:** field catalogue at 4 scopes, polymorphic set/remove value, custom task types [t9-custom-fields] ([582127e](https://github.com/trustxai/clickup-mcp/commit/582127e57d172376bd5c3572d67d9dcfebd95f6f))
* **docs:** ClickUp Docs v3 tools [t5-docs] ([10ad34c](https://github.com/trustxai/clickup-mcp/commit/10ad34c3d263035dfc9404f47e0889f014c199c1))
* **folders:** implement Folders CRUD + folder templates tools [t2-folders] ([4871897](https://github.com/trustxai/clickup-mcp/commit/487189743ec5a44498ede959093d42f37724324d))
* **goals:** implement Goals + Key Results CRUD tools [t12-goals] ([d118e82](https://github.com/trustxai/clickup-mcp/commit/d118e821db9d3778a79e1267cb9d61a324b37bbc))
* **guests,users:** implement Enterprise-gated guests and users tool modules [t14-guests-users] ([e2e6117](https://github.com/trustxai/clickup-mcp/commit/e2e6117395c16cb4d7c906c6ab5d59d95094d678))
* **lists:** implement Lists tool module [t3-lists] ([d00a77b](https://github.com/trustxai/clickup-mcp/commit/d00a77b747bb3fcc44c0964b6050bb2b243b4e10))
* **members:** implement members/user-groups/custom-roles tools [t13-members-groups] ([773e92c](https://github.com/trustxai/clickup-mcp/commit/773e92c6b940223029d9a1685fce4d81c7dd7b29))
* Phase 0 foundation (spine, stub registry, oracle, CI, release tooling) ([9bce43c](https://github.com/trustxai/clickup-mcp/commit/9bce43cc2493f15029483aa6f37878949ea331bb))
* **relationships,attachments:** task dependencies/links + file attachments [t11-relationships-attachments] ([64ea18b](https://github.com/trustxai/clickup-mcp/commit/64ea18b428c24ad148548b1956ecfd00c267a89d))
* **spaces:** Spaces CRUD tools [t1-spaces] ([37a3d9e](https://github.com/trustxai/clickup-mcp/commit/37a3d9e2d434abddb0cb227f2d18f50a8bfdaa86))
* **tasks:** task CRUD + workspace filters [t6-tasks-core] ([c17c20f](https://github.com/trustxai/clickup-mcp/commit/c17c20fc2799504b313353c21ea3e0060a327013))
* **time-tracking:** implement Time Tracking 2.0 + time estimates tools [t10-time-tracking] ([5f7c032](https://github.com/trustxai/clickup-mcp/commit/5f7c0328793cbae2999a640a1032d1478d2cd73d))
* **views:** implement Views tool module (create/get/update/delete) [t4-views] ([7ba2ab4](https://github.com/trustxai/clickup-mcp/commit/7ba2ab43017f1d0344ebc33a0923d28d95714188))
* **webhooks,workspace:** implement webhook CRUD + workspace admin tools [t15-webhooks-admin] ([edbbd4d](https://github.com/trustxai/clickup-mcp/commit/edbbd4d0965672852bf9861c140898af813da741))


### Bug Fixes

* **chat-messages:** top-level resolved field, subtype shape docs, emoji path encoding ([4b9cc74](https://github.com/trustxai/clickup-mcp/commit/4b9cc74b64381261853a5a869f8ff7c6bbc9b455))
* **docs:** apply the _cap byte guard to search_docs and page_listing JSON ([bf99a44](https://github.com/trustxai/clickup-mcp/commit/bf99a44d31dcc5f11ffb8556d422c76da9506d22))
* **folders:** drop no-op override_statuses from Create Folder ([7866fa1](https://github.com/trustxai/clickup-mcp/commit/7866fa134f26edd748b7819fc2218b4326d2c09d))
* **goals:** coerce boolean key result steps to 0/1 ([651ea34](https://github.com/trustxai/clickup-mcp/commit/651ea34efd797d3c90ce49397417a11e20a43b8d))
* rename distribution to amazing-clickup-mcp ([bd17de4](https://github.com/trustxai/clickup-mcp/commit/bd17de434179847948e99a52de075d0d0a3dcda4))
* **tasks:** require at least 2 task ids for bulk time-in-status ([b158008](https://github.com/trustxai/clickup-mcp/commit/b15800874b101ea8f8c87ff1bc3243cb2311cb48))

## Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries are generated automatically by release-please from conventional commits.
