# Node.js spec 规范

当 `<lang>=nodejs` 时，spec 初稿应优先采用 openEuler Node.js 包风格模板；可参考 `nodejsporter` 的模板结构，但**不直接复用其依赖映射结论**。

## 1. 适用范围
该模板优先适用于：
- 存在 `package.json`
- 单包仓库
- 纯 JavaScript 包
- 不包含 `binding.gyp`、`node-gyp`、`nan`、`node-addon-api`
- 不使用 `workspaces` / monorepo

若检测到 native addon 或 workspace/monorepo，不能直接使用该模板，需进入特殊分支处理。

## 2. spec 头部与命名规则
Node.js 模板应优先采用以下结构：

```spec
%{?nodejs_find_provides_and_requires}
%global packagename <upstream_name>

Name:           nodejs-<normalized_name>
Version:        <version>
Release:        1%{?dist}
Summary:        <summary>
License:        <license>
URL:            <url>
Source0:        <source0>

ExclusiveArch:  %{nodejs_arches} noarch
BuildArch:      noarch

BuildRequires:  nodejs-packaging
```

规则：
- 包名统一使用 `nodejs-<normalized_name>`
- `packagename` 保留上游 npm 包名
- `Source0` 优先与当前流程实际下载/打包的源码 tarball 保持一致
- 若使用 npm tarball 结构，`Source0` 可参考 npm registry tarball URL 规则
- 对 scoped package（如 `@scope/pkg`）必须先做 RPM 名称规范化，不能直接原样写入 `Name`

## 3. `%prep` 规则
Node.js 模板默认使用：

```spec
%prep
%autosetup -n package
```

说明：
- 该规则适用于标准 npm tarball 解包目录名为 `package` 的情形
- 若当前流程生成的 source tarball 解包目录并非 `package`，则必须改为真实目录名，不能强行沿用 `-n package`

## 4. `%build` 规则
纯 JavaScript 包默认：

```spec
%build
# nothing to do!
```

只有在源码中能明确识别出稳定、可控的构建步骤时，才允许扩展 `%build`，例如：
- `package.json` 中存在明确的 `build` / `compile` 脚本
- 且该脚本不依赖额外未纳入控制的前端工具链

默认不要自动生成 `npm test`、`npm run build` 等命令。

## 5. `%install` 规则
Node.js 模板的 `%install` 应至少覆盖以下动作：
- 统一规范化 `LICENSE`
- 若存在 `bin/`，安装到 `%{_bindir}`
- 安装源码内容到 `%{nodejs_sitelib}/%{packagename}`
- 调用 `%nodejs_symlink_deps`

推荐结构：

```spec
%install
if [ -f license ]; then
    mv license LICENSE
fi
if [ -f License ]; then
    mv License LICENSE
fi

if [ -d bin ]; then
    mkdir -p %{buildroot}%{_bindir}
    cp -ar bin/* %{buildroot}%{_bindir}
fi

mkdir -p %{buildroot}%{nodejs_sitelib}/%{packagename}
cp -ra * %{buildroot}%{nodejs_sitelib}/%{packagename}

%nodejs_symlink_deps
```

注意：
- `cp -ra *` 只是模板初稿策略；若包含明显不应入包的目录（如测试数据、CI 配置、缓存目录等），应在后续修正中排除
- 若项目包含 native addon、预编译产物或特殊安装布局，不能直接沿用该默认安装逻辑

## 6. `%check` 规则
默认仅执行基础依赖链接检查：

```spec
%check
%nodejs_symlink_deps --check
```

不要默认执行：
- `npm test`
- `npm run test`
- 任何依赖 devDependencies、大型测试框架、网络环境或浏览器环境的测试命令

如需启用测试，应单独判断并显式添加。

## 7. `%files` 规则
Node.js 模板初稿优先采用**显式 `%files`**，不要默认使用动态 filelist。

推荐初稿：

```spec
%files
%license LICENSE
%{nodejs_sitelib}/%{packagename}
```

若存在 `bin/` 安装内容，再补充：

```spec
%{_bindir}/*
```

说明：
- 显式 `%files` 更稳定、可读、便于后续 rpmlint / `%files` 修正
- 动态 filelist 可作为兜底方案，但不应作为默认首选

## 8. 依赖分析规则
Node.js 依赖分析可以参考 `nodejsporter` 的**元数据提取思路**，但**不能直接复用其 `Requires` / `BuildRequires` 映射逻辑**。

规则：
- 从本地 `package.json` 中提取：
  - `dependencies`
  - `devDependencies`
  - `peerDependencies`
  - `optionalDependencies`
- 这些依赖只作为**候选输入**，供后续预检与 resolver 使用
- 不要机械生成：
  - `dependencies -> BuildRequires`
  - `devDependencies -> Requires`

建议分类：
- `dependencies`：运行时依赖候选
- `devDependencies`：构建/测试依赖候选
- `peerDependencies`：人工审查候选
- `optionalDependencies`：保守处理，不默认写入 spec

最终是否写入 `BuildRequires` / `Requires`，仍以：
- 容器内 openEuler 软件源真值
- `dnf builddep`
- `rpmbuild`
- 缺包递归引入

的结果为准。

## 9. 不直接采用的 `nodejsporter` 行为
以下行为只可作为参考，不应直接纳入当前主流程：
- 将 `dependencies` / `devDependencies` 直接翻译为 RPM 依赖字段
- 直接使用 `--build` / `--buildinstall` 替代当前构建链路
- 默认使用动态 `filelist.lst`
- 默认假设所有 Node.js 包都为纯 JS / noarch

## 10. 结果定位
Node.js 模板生成的 spec 只是**初稿**，后续仍必须经过：
- `rpmlint`
- `dnf builddep`
- `rpmbuild`
- 依赖递归引入
- 失败分类修复

不能把模板生成结果视为最终可用 spec。
