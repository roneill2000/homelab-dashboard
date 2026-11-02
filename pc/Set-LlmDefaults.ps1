# Run on the Windows gaming PC. Changes presets only; does not stop any process.
$ErrorActionPreference = 'Stop'
$path = 'C:\llm\models.ini'
$original = [IO.File]::ReadAllText($path)
$newline = if ($original.Contains("`r`n")) { "`r`n" } else { "`n" }
$settings = @{
    'qwen2.5-coder-14b' = @{ 'sleep-idle-seconds' = '300' }
    'qwen3.6-35b-moe' = @{
        'sleep-idle-seconds' = '300'; 'temperature' = '1.0'; 'top-p' = '0.95'
        'top-k' = '20'; 'min-p' = '0.0'; 'presence-penalty' = '1.5'; 'repeat-penalty' = '1.0'
    }
    'qwen3.8-27b' = @{ 'sleep-idle-seconds' = '300'; 'reasoning-effort' = 'medium' }
}
$seen = @{}
$pattern = '(?ms)^[ \t]*\[(?<name>[^\]\r\n]+)\][^\r\n]*(?:\r?\n|$)(?<body>.*?)(?=^[ \t]*\[|\z)'
$updated = [regex]::Replace($original, $pattern, [Text.RegularExpressions.MatchEvaluator]{
    param($match)
    $name = $match.Groups['name'].Value.Trim()
    if (-not $settings.ContainsKey($name)) { return $match.Value }
    if ($seen.ContainsKey($name)) { throw "Duplicate section: $name. File unchanged." }
    $seen[$name] = $true
    $body = $match.Groups['body'].Value
    $header = $match.Value.Substring(0, $match.Value.Length - $body.Length)
    foreach ($key in ($settings[$name].Keys | Sort-Object)) {
        $line = "$key = $($settings[$name][$key])"
        $keyPattern = '(?m)^[ \t]*' + [regex]::Escape($key) + '[ \t]*=[^\r\n]*'
        if ([regex]::IsMatch($body, $keyPattern)) {
            $body = [regex]::Replace($body, $keyPattern, $line)
        } else {
            if ($body.Length -gt 0 -and -not $body.EndsWith("`n")) { $body += $newline }
            $body += $line + $newline
        }
    }
    if (-not $header.EndsWith("`n")) { $header += $newline }
    return $header + $body
})
foreach ($name in $settings.Keys) {
    if (-not $seen.ContainsKey($name)) { throw "Missing section: $name. File unchanged." }
}
if ($updated -ceq $original) {
    Write-Host 'Presets already match. Restart the router after any active chat to apply them.'
    return
}
$suffix = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N')
$backup = "$path.$suffix.bak"
$temp = "$path.$suffix.tmp"
try {
    [IO.File]::WriteAllText($temp, $updated, [Text.UTF8Encoding]::new($false))
    if ([IO.File]::ReadAllText($path) -cne $original) { throw 'Presets changed during this operation; retry.' }
    [IO.File]::Replace($temp, $path, $backup)
    if ([IO.File]::ReadAllText($path) -cne $updated) { throw "Verification failed. Restore $backup." }
} finally {
    if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }
}
Write-Host "Saved and verified. Backup: $backup"
Write-Host 'Medium reasoning; MoE thinking defaults; five-minute idle sleep for all three models.'
Write-Host 'No process was stopped. Restart your llama.cpp router after the current chat finishes.'
