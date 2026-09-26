# Worktree 工作流

主仓库 `mewhelp/` 停在 `main` 分支,**基本不动**。每章新功能从 `main` 开出一个独立 worktree,做完 merge 回来,做砸了整个目录删掉。

## 目录约定

```
C:\Users\27497\projects\
├── mewhelp\              ← 主仓库(main),平时只在这里 merge
│   ├── .git\
│   └── ...
└── mewhelp-wt\           ← 所有 worktree 放这里,一个功能一间
    ├── ch01-rag\
    ├── ch02-agent\
    └── ch03-eval\
```

**两个目录必须是兄弟关系**,不能把 worktree 放进仓库内部,否则 `git status` 会看到一堆东西。

## 日常命令

### 开一章新功能

```bash
cd C:/Users/27497/projects/mewhelp
git worktree add ../mewhelp-wt/ch05-eval -b ch05-eval
```

这会:
- 从当前 `main` 的最新提交拉一份完整工作区到 `../mewhelp-wt/ch05-eval`
- 同时新建一个叫 `ch05-eval` 的分支

然后 `cd ../mewhelp-wt/ch05-eval` 就能开工了,和普通仓库没有任何区别。

### 做完 → 合并回 main

```bash
cd C:/Users/27497/projects/mewhelp
git merge ch05-eval
```

合并成功后再清掉 worktree(见下)。

### 改砸了 → 整间扔掉

```bash
cd C:/Users/27497/projects/mewhelp
git worktree remove ../mewhelp-wt/ch05-eval --force
git branch -D ch05-eval
```

`--force` 会连同未提交的修改一起删。**主仓库一个字节都没动**,`main` 还停在上一章完成的状态。

### 查看当前有哪些 worktree

```bash
git worktree list
```

### 目录被手动删了 / 路径对不上

```bash
git worktree prune          # 清理已失效的记录
git worktree repair         # 修复路径
```

## 为什么这么用

- **隔离**:第 5 章改崩了,不影响第 1~4 章的成果。比 `git stash` 干净,比"复制一份文件夹"安全(复制出来的副本没有 `.git`,或者会共用 `.git` 导致状态错乱)。
- **不用切分支**:传统做法是 `git checkout ch05`,但同一时间只能有一个分支的工作区。worktree 让你能同时开着第 3 章和第 5 章,一个跑测试一个写代码。
- **省空间**:所有 worktree 共享同一个 `.git` 对象库,不是真的复制代码。

## 注意事项

1. **同一个分支不能同时被两个 worktree 检出。** 想在第 2 个目录里开同一个分支,用 `git worktree add --detach <path> <branch>`。
2. **删 worktree 用 `git worktree remove`,不要直接 `rm -rf`。** 直接删会留下失效记录(虽然能 `prune` 修复,但多一步)。
3. **worktree 里的改动必须先 commit(或 merge)**。删 worktree 时未提交的改动会丢,`--force` 更是直接抹掉。
4. **主仓库也要偶尔更新**。合并完记得确保你站在 `main` 上,下次开新 worktree 才能基于最新成果。

## 常用速查

| 想干什么 | 命令(在主仓库目录执行) |
| --- | --- |
| 开新章节 | `git worktree add ../mewhelp-wt/<名字> -b <名字>` |
| 看有哪些 | `git worktree list` |
| 合并 | `git merge <名字>` |
| 成功后清理 | `git worktree remove ../mewhelp-wt/<名字>` |
| 失败后丢弃 | `git worktree remove ../mewhelp-wt/<名字> --force` + `git branch -D <名字>` |
| 修记录 | `git worktree prune` / `git worktree repair` |
