param(
  [Parameter(Mandatory = $true)]
  [string]$Path,

  [int]$TailLines = 1000
)

chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$Host.UI.RawUI.WindowTitle = [string]::Concat('Agent ', [char]0x7ec8, [char]0x7aef, [char]0x4fe1, [char]0x606f)

function Write-AgentLogLine {
  param([AllowNull()][string]$Line)

  if ($null -eq $Line) { return }

  $color = 'Gray'
  if ($Line -match 'ERROR|Exception|Traceback|failed|失败|错误|ERR ') {
    $color = 'Red'
  } elseif ($Line -match 'WARNING|WARN|警告|timeout|超时') {
    $color = 'Yellow'
  } elseif ($Line -match '^\s*INFO:\s+\d+\.\d+\.\d+\.\d+:\d+\s+-\s+"(GET|POST|PUT|DELETE|PATCH)') {
    $color = 'Cyan'
  } elseif ($Line -match '\s-\sINFO\s-' -or $Line -match '^INFO:') {
    $color = 'Green'
  } elseif ($Line -match 'Agent started|Agent 已启动|正在启动|Application startup complete|Uvicorn running') {
    $color = 'Magenta'
  } elseif ($Line -match '^\[ssl_bypass\]') {
    $color = 'DarkCyan'
  } elseif ($Line -match '^=+$') {
    $color = 'DarkGray'
  } elseif ($Line -match 'llm|LLM|tool|工具|subagent|MCP') {
    $color = 'White'
  }

  Write-Host $Line -ForegroundColor $color
}

if (-not (Test-Path -LiteralPath $Path)) {
  Write-AgentLogLine "日志文件不存在: $Path"
  return
}

$TailLines = [Math]::Max(1, $TailLines)
Clear-Host
Get-Content -LiteralPath $Path -Encoding UTF8 -Tail $TailLines -Wait | ForEach-Object {
  Write-AgentLogLine $_
}
