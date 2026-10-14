pip install "pymilvus>=2.6,<3.0"<#
scripts/pull_images.ps1   (本文件为 UTF-8 with BOM)

用途：
    国内网络环境下，某个镜像加速器不放行某些镜像（典型报错：403 Forbidden，
    例如阿里云加速器拒绝 docker.io/minio/minio）时，用这个脚本"多源试探"：
        对每个需要的镜像，依次尝试多个公共镜像前缀，
        哪个拉成功，就把它 docker tag 成 compose 里写的标准镜像名。
    之后 `docker compose ... up -d` 会直接命中本地镜像，不再联网拉取。

用法：
    powershell -ExecutionPolicy Bypass -File scripts\pull_images.ps1
    powershell -ExecutionPolicy Bypass -File scripts\pull_images.ps1 -Only minio,milvus
    powershell -ExecutionPolicy Bypass -File scripts\pull_images.ps1 -Mirrors "docker.m.daocloud.io","hub.rat.dev"
    powershell -ExecutionPolicy Bypass -File scripts\pull_images.ps1 -Force      # 已存在的也重拉

常用公共镜像前缀（按顺序尝试，失败自动换下一个）：
    docker.m.daocloud.io      DaoCloud 公共代理（较稳，支持第三方命名空间）
    docker.1panel.live        1Panel 公共代理
    hub.rat.dev               rat.dev 公共代理
    dockerpull.org / docker.1ms.run

说明：
    * 只对 Docker Hub 简写镜像做前缀替换；quay.io 这类带域名的镜像走 -QuayMirrors。
    * 空字符串 "" 作为前缀表示"直连"，即使用 Docker 配置里的 registry-mirrors。
    * 国家/校园网络策略不同，若全部失败，请看脚本结尾的提示（离线 save/load 方案）。
#>
param(
    [string[]]$Mirrors = @(
        "docker.m.daocloud.io",
        "docker.1panel.live",
        "hub.rat.dev",
        "dockerpull.org",
        ""
    ),
    [string[]]$QuayMirrors = @(
        "quay.m.daocloud.io"
    ),
    # 只处理名字里包含这些关键字的镜像，例如 -Only minio,milvus
    [string[]]$Only = @(),
    # 已存在的镜像是否强制重新拉取
    [switch]$Force
)

$ErrorActionPreference = "Continue"

# ---------------------------------------------------------------------------
# 本项目（混合模式）需要的镜像清单
# ---------------------------------------------------------------------------
$Images = @(
    "milvusdb/milvus:v2.4.13",
    "quay.io/coreos/etcd:v3.5.5",
    "minio/minio:RELEASE.2023-03-20T20-16-18Z",
    "influxdb:3-core"
)

function Test-ImageExist([string]$name) {
    docker image inspect $name 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Invoke-PullCandidate([string]$candidate, [string]$canonical) {
    Write-Host ("      try  {0}" -f $candidate) -ForegroundColor DarkGray
    $output = docker pull $candidate 2>&1
    if ($LASTEXITCODE -ne 0) {
        # 只回显最后一行错误，避免刷屏
        $last = ($output | Select-Object -Last 1)
        if ($last) { Write-Host ("      fail {0}" -f $last) -ForegroundColor DarkGray }
        return $false
    }
    if ($candidate -ne $canonical) {
        Write-Host ("      tag  {0}  ->  {1}" -f $candidate, $canonical) -ForegroundColor Yellow
        docker tag $candidate $canonical 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
    }
    return (Test-ImageExist $canonical)
}

function Get-Candidates([string]$image, [string[]]$mirrors, [string[]]$quayMirrors) {
    $list = New-Object System.Collections.ArrayList

    if ($image -match '^[^/]+[.:][^/]*/') {
        # 已带注册表域名（例如 quay.io/coreos/etcd）
        if ($image -like "quay.io/*") {
            $rest = $image.Substring("quay.io/".Length)
            foreach ($qm in $quayMirrors) {
                if ($qm) { [void]$list.Add("$qm/$rest") }
            }
        }
        [void]$list.Add($image)
        return $list
    }

    # Docker Hub 简写（例如 minio/minio、influxdb）
    foreach ($m in $mirrors) {
        if (-not $m) { continue }
        if ($image -notmatch "/") {
            [void]$list.Add("$m/library/$image")   # 官方库镜像要补 library/
        } else {
            [void]$list.Add("$m/$image")
        }
    }
    [void]$list.Add($image)   # 最后再直连试一次（走 Docker 自己的 registry-mirrors）
    return $list
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==== 智能健康管家 · 中间件镜像预拉取 ====" -ForegroundColor Cyan
Write-Host ("镜像前缀顺序: {0}" -f (($Mirrors | ForEach-Object { if ($_) { $_ } else { "<直连>" } }) -join "  ->  "))

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "找不到 docker 命令，请确认 Docker Desktop 已启动。" -ForegroundColor Red
    exit 2
}

$targets = $Images
if ($Only.Count -gt 0) {
    $targets = $Images | Where-Object { $img = $_; ($Only | Where-Object { $img -like "*$_*" }).Count -gt 0 }
    if ($targets.Count -eq 0) {
        Write-Host ("-Only 没有匹配到任何镜像：{0}" -f ($Only -join ",")) -ForegroundColor Red
        exit 2
    }
}

$results = @()
foreach ($image in $targets) {
    Write-Host ""
    Write-Host ("[镜像] {0}" -f $image) -ForegroundColor Cyan

    if ((-not $Force) -and (Test-ImageExist $image)) {
        Write-Host "      已存在，跳过（需要重拉请加 -Force）" -ForegroundColor Green
        $results += [pscustomobject]@{ Image = $image; Status = "已存在"; Source = "local" }
        continue
    }

    $candidates = Get-Candidates $image $Mirrors $QuayMirrors
    $done = $false
    foreach ($candidate in $candidates) {
        if (Invoke-PullCandidate $candidate $image) {
            $results += [pscustomobject]@{ Image = $image; Status = "成功"; Source = $candidate }
            $done = $true
            break
        }
    }
    if (-not $done) {
        $results += [pscustomobject]@{ Image = $image; Status = "全部失败"; Source = "-" }
    }
}

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==== 结果汇总 ====" -ForegroundColor Cyan
$results | Format-Table -AutoSize

$failed = $results | Where-Object { $_.Status -eq "全部失败" }
if ($failed.Count -eq 0) {
    Write-Host "全部就绪，可以启动：" -ForegroundColor Green
    Write-Host "  docker compose -f docker-compose.middleware.yml up -d"
    exit 0
}

Write-Host "以下镜像所有镜像源都失败了：" -ForegroundColor Red
$failed | ForEach-Object { Write-Host ("  - {0}" -f $_.Image) -ForegroundColor Red }
Write-Host ""
Write-Host "下一步建议（按顺序试）：" -ForegroundColor Yellow
Write-Host "  1) 换更多镜像前缀："
Write-Host '     powershell -File scripts\pull_images.ps1 -Mirrors "docker.1ms.run","docker.io.docker-mirror.com","docker.1panel.dev"'
Write-Host "  2) 在 Docker Desktop -> Settings -> Docker Engine 里把 registry-mirrors 换成"
Write-Host "     https://docker.m.daocloud.io （阿里云个人加速器对第三方镜像常返回 403）"
Write-Host "  3) 离线方案（有网机器上导出，本机导入）："
Write-Host "     # 有网机器："
Write-Host "     docker pull milvusdb/milvus:v2.4.13"
Write-Host "     docker pull quay.io/coreos/etcd:v3.5.5"
Write-Host "     docker pull minio/minio:RELEASE.2023-03-20T20-16-18Z"
Write-Host "     docker pull influxdb:3-core"
Write-Host "     docker save -o health-butler-images.tar milvusdb/milvus:v2.4.13 quay.io/coreos/etcd:v3.5.5 minio/minio:RELEASE.2023-03-20T20-16-18Z influxdb:3-core"
Write-Host "     # 本机：docker load -i health-butler-images.tar"
Write-Host "  4) 想绕过 Milvus 依赖：.env 里设 RAG_MODE=keyword（知识检索走 MySQL，功能闭环仍在）"
exit 1
