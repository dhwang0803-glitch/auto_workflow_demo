# compose_demo_video.ps1 — assemble the 30-second demo mp4 from raw
# Playwright recordings.
#
# Inputs (produced by `pnpm run record:demo`):
#   tmp/demo/raw/alice.webm
#   tmp/demo/raw/bob.webm
#   tmp/demo/markers.json
#
# Output:
#   tmp/demo/final.mp4
#
# Algorithm:
#   1. Load markers.json (scene + wait spans, ms relative to each
#      context's recording start).
#   2. Drop every `kind=wait` marker (those are LLM round-trips we
#      excise so the 30-second cut isn't dominated by dead time).
#   3. Sort scene markers by storyboard order — derived from the scene
#      name prefix (scene1 < scene2a < scene2b < scene2c < scene3a ...).
#   4. For each scene: ffmpeg -ss/-to trim from the matching webm into
#      an h264 mp4 segment.
#   5. Concat the segments into final.mp4 (1080p30, no audio).
#
# Requires: ffmpeg on PATH.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$RawDir = Join-Path $RepoRoot "tmp/demo/raw"
$WorkDir = Join-Path $RepoRoot "tmp/demo/segments"
$MarkersPath = Join-Path $RepoRoot "tmp/demo/markers.json"
$FinalPath = Join-Path $RepoRoot "tmp/demo/final.mp4"

foreach ($p in @($RawDir, $MarkersPath)) {
    if (-not (Test-Path $p)) {
        throw "missing input: $p (did you run pnpm run record:demo?)"
    }
}

# Require both webms.
foreach ($name in @("alice.webm", "bob.webm")) {
    if (-not (Test-Path (Join-Path $RawDir $name))) {
        throw "missing $name in $RawDir — Playwright's webms may not have been renamed; check the recorder log"
    }
}

# Reset work dir so a re-run doesn't concat stale segments.
if (Test-Path $WorkDir) { Remove-Item $WorkDir -Recurse -Force }
New-Item -ItemType Directory -Path $WorkDir | Out-Null

$markers = (Get-Content $MarkersPath -Raw | ConvertFrom-Json).markers

# Storyboard order — keeps cross-cuts in the correct sequence regardless
# of the chronological order the recorder hit them in.
$order = @(
    "scene1_hook",
    "scene2a_skills",
    "scene2b_chat_send",
    "scene2c_clarify_shown",
    "scene2d_pick",
    "scene2e_dag",
    "scene3a_edit",
    "scene3b_save",
    "scene3c_activate",
    "scene3d_chat_send",
    "scene3e_dag",
    "scene4a_share",
    "scene4b_chat_send",
    "scene4c_dag",
    "scene5_close"
)

$scenes = $markers | Where-Object { $_.kind -eq "scene" }
$concatLines = New-Object System.Collections.Generic.List[string]
$i = 0

foreach ($name in $order) {
    $m = $scenes | Where-Object { $_.scene -eq $name } | Select-Object -First 1
    if ($null -eq $m) {
        Write-Warning "scene '$name' missing from markers — skipping"
        continue
    }
    $src = Join-Path $RawDir "$($m.context).webm"
    $startSec = [math]::Round($m.startMs / 1000.0, 3)
    $endSec = [math]::Round($m.endMs / 1000.0, 3)
    if (($endSec - $startSec) -lt 0.1) {
        Write-Host ("[--] {0,-22} skipped (zero-duration)" -f $name)
        continue
    }
    $seg = Join-Path $WorkDir ("seg_{0:d2}_{1}.mp4" -f $i, $name)

    Write-Host ("[{0:d2}] {1,-22} {2} {3:F2}s → {4:F2}s" -f $i, $name, $m.context, $startSec, $endSec)

    # Re-encode each segment so concat doesn't need matching keyframes.
    # `-an` because the recordings are silent and the final has no audio.
    & ffmpeg -hide_banner -loglevel error -y `
        -ss $startSec -to $endSec -i $src `
        -an -c:v libx264 -preset fast -crf 20 `
        -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=30" `
        $seg
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed on segment $seg" }

    $concatLines.Add("file '$($seg.Replace('\','/'))'")
    $i += 1
}

if ($concatLines.Count -eq 0) {
    throw "no scenes matched — markers.json may be empty or stale"
}

$concatFile = Join-Path $WorkDir "concat.txt"
[System.IO.File]::WriteAllLines($concatFile, $concatLines)

Write-Host ""
Write-Host "Concatenating $($concatLines.Count) segments → $FinalPath"

& ffmpeg -hide_banner -loglevel error -y `
    -f concat -safe 0 -i $concatFile `
    -c:v libx264 -preset fast -crf 20 -pix_fmt yuv420p `
    $FinalPath
if ($LASTEXITCODE -ne 0) { throw "ffmpeg concat failed" }

$dur = (& ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $FinalPath)
Write-Host ""
Write-Host "DONE  $FinalPath  ($([math]::Round([double]$dur, 2))s)"
