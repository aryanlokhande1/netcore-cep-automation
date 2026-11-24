---
name: netcore-sms
description: Fixed-payload Netcore SMS campaign review through Preview.
---

This baseline provides guidance only. The controller must verify the protected tenant,
exact unused campaign name, future timestamp, and scheduling UI timezone before entry.
Use only the stage-scoped MCP tools and require independent UI readback before advancing.
The controller owns transitions and the one Preview attempt. Stop on uncertain writes.
