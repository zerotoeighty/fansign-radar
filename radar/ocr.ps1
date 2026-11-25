# 윈도우 기본 OCR(Windows.Media.Ocr, 한국어)로 이미지 글자를 읽어 줄 단위로 출력한다. 무료·오프라인.
# 사용: powershell -NoProfile -ExecutionPolicy Bypass -File ocr.ps1 <이미지1> [이미지2 ...]
# 세로로 아주 긴 공지 이미지는 OCR 한계(가로·세로 최대 픽셀) 안으로 잘라서 읽는다.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Paths)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type -AssemblyName System.Drawing
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) {
    $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op))
    $task.Wait() | Out-Null
    $task.Result
}

$lang = New-Object Windows.Globalization.Language 'ko'
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
$max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
$tmp = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "fansign_ocr_" + [guid]::NewGuid().ToString('N') + ".png")

function Read-Png([string]$png) {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($png)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        foreach ($l in $res.Lines) { $l.Text }
    } finally { $stream.Dispose() }
}

foreach ($p in $Paths) {
    try {
        $img = [System.Drawing.Image]::FromFile((Resolve-Path $p).Path)
        try {
            # 가로가 넓으면 줄이고(글자 크기 유지 위해 최대 1600), 세로는 조각으로 나눈다
            $scale = [Math]::Min(1.0, 1600.0 / $img.Width)
            $w = [int]($img.Width * $scale); $h = [int]($img.Height * $scale)
            $chunk = [Math]::Min($max, 2400); $overlap = 120
            for ($y = 0; $y -lt $h; $y += ($chunk - $overlap)) {
                $ch = [Math]::Min($chunk, $h - $y)
                if ($ch -lt 40) { break }
                $bm = New-Object System.Drawing.Bitmap $w, $ch
                $g = [System.Drawing.Graphics]::FromImage($bm)
                $g.InterpolationMode = 'HighQualityBicubic'
                $g.Clear([System.Drawing.Color]::White)
                $src = New-Object System.Drawing.RectangleF 0, ($y / $scale), $img.Width, ($ch / $scale)
                $dst = New-Object System.Drawing.RectangleF 0, 0, $w, $ch
                $g.DrawImage($img, $dst, $src, [System.Drawing.GraphicsUnit]::Pixel)
                $g.Dispose(); $bm.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png); $bm.Dispose()
                Read-Png $tmp
                if ($y + $ch -ge $h) { break }
            }
        } finally { $img.Dispose() }
        "@@END@@"
    } catch {
        "@@ERROR@@ $($_.Exception.Message)"
    }
}
Remove-Item $tmp -ErrorAction SilentlyContinue
