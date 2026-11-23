# Instala LimpiaPaginas en Windows: entorno virtual, dependencias y PyTorch/ONNX
# Runtime con GPU si hay una NVIDIA. Se puede repetir sin problema.
#   instalar.bat          detecta la GPU
#   instalar.bat -Cpu     fuerza la instalacion solo para CPU
param([switch]$Cpu)

Set-Location $PSScriptRoot

# --- Python 3.11 o superior ---------------------------------------------------
$python = $null
$candidatos = @(
    @("py", "-3.12"), @("py", "-3.11"), @("py", "-3.13"), @("python"), @("python3")
)
foreach ($c in $candidatos) {
    $exe = $c[0]
    $extra = @()
    if ($c.Count -gt 1) { $extra = @($c[1]) }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    & $exe @extra -c "import sys; sys.exit(sys.version_info < (3, 11))" 2>$null
    if ($LASTEXITCODE -eq 0) { $python = $c; break }
}
if (-not $python) {
    Write-Host "Hace falta Python 3.11 o superior (https://www.python.org/downloads/)." -ForegroundColor Red
    exit 1
}
$exe = $python[0]
$extra = @()
if ($python.Count -gt 1) { $extra = @($python[1]) }
Write-Host "== Python: $(& $exe @extra --version)"

# --- Entorno virtual y dependencias base --------------------------------------
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $exe @extra -m venv .venv }
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
Write-Host "== Instalando dependencias base"
& $py -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "Fallo al instalar las dependencias." -ForegroundColor Red; exit 1 }

# --- GPU -----------------------------------------------------------------------
$gpu = $false
if (-not $Cpu -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    $nombre = & nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>$null | Select-Object -First 1
    if ($LASTEXITCODE -eq 0 -and $nombre) { $gpu = $true; Write-Host "== GPU detectada: $nombre" }
}
if (-not $gpu) { Write-Host "== Sin GPU NVIDIA (o instalacion forzada a CPU)" }

if ($gpu) {
    # En Windows el PyTorch de PyPI es solo para CPU: hay que pedirlo al indice de PyTorch.
    # Se prueban las versiones de CUDA de mas nueva a mas antigua; la RTX 5090 necesita cu128 o posterior.
    $instalado = $false
    foreach ($cuda in @("cu132", "cu130", "cu129", "cu128")) {
        Write-Host "== Probando PyTorch con $cuda"
        & $py -m pip install --quiet torch --index-url "https://download.pytorch.org/whl/$cuda" 2>$null
        if ($LASTEXITCODE -eq 0) { $instalado = $true; break }
    }
    if (-not $instalado) {
        Write-Host "   No se pudo instalar PyTorch con CUDA; se instala la version de CPU." -ForegroundColor Yellow
        & $py -m pip install --quiet torch
    }
    & $py -m pip uninstall --quiet -y onnxruntime 2>$null
    & $py -m pip install --quiet onnxruntime-gpu
    if ($LASTEXITCODE -ne 0) { & $py -m pip install --quiet onnxruntime }
} else {
    Write-Host "== Instalando PyTorch y ONNX Runtime para CPU"
    & $py -m pip install --quiet torch --index-url "https://download.pytorch.org/whl/cpu"
    if ($LASTEXITCODE -ne 0) { & $py -m pip install --quiet torch }
    & $py -m pip install --quiet onnxruntime
}

Write-Host ""
& $py -m limpiapaginas diagnostico
Write-Host "Listo. Arranca con iniciar.bat"
