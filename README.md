# Skills Manager

[中文](#中文) | [English](#english)

## 中文

Skills Manager 是一个用于管理独立 Codex Skill 的轻量工具。它将 Skill 按作用域区分为通用型 Skill 和项目型 Skill，并通过规范库与作用域符号链接控制 Skill 的可发现范围。

它还支持 Skill 分组、迁移、校验和功能重叠检查，帮助减少多个相似 Skill 同时存在时产生的路由歧义。

### 主要能力

- **通用型 Skill**：存放在中心规范库，并通过全局入口对所有项目可用。
- **项目型 Skill**：绑定到指定项目或模块，只在该范围内暴露；也支持在项目尚未创建时预安装。
- **规范库管理**：每个 Skill 保留一个规范副本，通过符号链接建立作用域入口。
- **Skill 分组**：使用扁平的 YAML 清单组织 Skill 组，并按全局或项目范围批量暴露。
- **功能重叠检查**：默认开启。先用 `name` 和 `description` 做本地候选筛选，再由 Agent 判断两个 Skill 是否会竞争同一类用户请求。
- **安全操作**：变更默认先预演；冲突需要明确确认；移除会进入可恢复备份，不会静默合并或永久删除。

### 高度功能重叠

两个 Skill 在以下条件同时满足时，会被视为高度功能重叠：

1. 处理相同或可互换的对象；
2. 核心动作重叠，或一个 Skill 包含另一个 Skill 的能力；
3. 常见用户请求可能同时触发两者；
4. 没有清晰的路由边界。

例如，“PPT 美化”和“PPT 优化”可能同时响应“把这个 PPT 做得更好看”，因此会提示用户选择保留两者、保留现有 Skill、保留新 Skill 或取消。

### 常用命令

```bash
python3 scripts/skills_manager.py status
python3 scripts/skills_manager.py overlap scan
python3 scripts/skills_manager.py overlap scan --candidate /path/to/skill
python3 scripts/skills_manager.py overlap set off
python3 scripts/skills_manager.py overlap set off --apply
```

`overlap scan` 是只读的词法候选筛选，不会自行判断语义，也不会自动安装、删除或替换 Skill。

### 边界

Skills Manager 管理独立 Skill，不负责插件或系统内置 Skill。它没有生命周期 Hook；标准 Agent 安装流程可以先将来源放入 staging，再交给 Skills Manager 检查，但直接调用底层安装脚本或手工复制文件可能绕过检查。

## English

Skills Manager is a lightweight tool for managing standalone Codex Skills. It classifies Skills by scope as global Skills or project Skills, then controls their discoverability through a canonical library and scoped directory symlinks.

It also supports Skill groups, migration, validation, and functional-overlap checks to reduce routing ambiguity when multiple Skills can answer similar user requests.

### Core capabilities

- **Global Skills**: stored in the central canonical library and exposed for use across projects.
- **Project Skills**: bound to a user-provided project or module root, with support for preinstalling a project Skill before that directory exists.
- **Canonical library**: keeps one canonical copy of each managed Skill and creates scoped symlink entry points.
- **Skill groups**: organizes Skills with flat YAML manifests and exposes groups globally or to a project.
- **Functional-overlap checks**: enabled by default. The script performs a local `name` and `description` screening, then the Agent determines whether two Skills compete for the same user intent.
- **Safe operations**: mutations are dry-run first, conflicts require explicit confirmation, and removals move Skills to recoverable backups instead of silently merging or permanently deleting them.

### High functional overlap

Two Skills are highly overlapping when all of the following are true:

1. They operate on the same or interchangeable object;
2. Their core actions overlap, or one Skill contains the other's capability;
3. Common user requests could plausibly trigger both;
4. There is no clear routing boundary between them.

For example, a “PPT beautification” Skill and a “PPT optimization” Skill may both respond to “make this presentation look better”, so Skills Manager asks whether to keep both, keep the existing Skill, keep the new Skill, or cancel.

### Common commands

```bash
python3 scripts/skills_manager.py status
python3 scripts/skills_manager.py overlap scan
python3 scripts/skills_manager.py overlap scan --candidate /path/to/skill
python3 scripts/skills_manager.py overlap set off
python3 scripts/skills_manager.py overlap set off --apply
```

`overlap scan` is a read-only lexical candidate screen. It does not make semantic decisions and never installs, removes, or replaces Skills by itself.

### Boundaries

Skills Manager manages standalone Skills, not plugins or bundled system Skills. It has no lifecycle Hook. The standard Agent installation workflow can stage a source before handing it to Skills Manager, while direct low-level installer calls or manual filesystem copies may bypass the check.
