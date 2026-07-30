[CmdletBinding()]
param(
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $OutputPath) {
    $OutputPath = Join-Path $projectRoot 'Pruefversand_HOME_2026-07-30.zip'
}
$outputFullPath = [System.IO.Path]::GetFullPath($OutputPath)
$checksumPath = $outputFullPath + '.sha256'
if (-not [string]::Equals(
    [System.IO.Path]::GetDirectoryName($outputFullPath),
    $projectRoot,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw 'Le paquet HOME doit etre cree directement a la racine du projet.'
}
if (Test-Path -LiteralPath $outputFullPath) {
    throw ('Le paquet existe deja : {0}' -f $outputFullPath)
}
if (Test-Path -LiteralPath $checksumPath) {
    throw ('La somme du paquet existe deja : {0}' -f $checksumPath)
}

$rootFiles = @(
    '.env.example',
    '.gitignore',
    'AGENTS.md',
    'compose.home.yml',
    'PLANS.md',
    'Plan_de_travail_Pruefprotokolle.md',
    'pyproject.toml',
    'README.md'
)
$sourceDirectories = @(
    'config',
    'docs',
    'sample_data',
    'scripts',
    'sql',
    'src',
    'tests',
    'tests_mysql'
)

function Test-AllowedPackagePath([string]$RelativePath) {
    $normalized = $RelativePath.Replace('\', '/')
    if ($normalized -eq '.env.example') {
        return $true
    }
    if ($normalized -match '(^|/)(__pycache__|upload|var|\.git)(/|$)') {
        return $false
    }
    if ($normalized -match '(^|/)\.env($|\.)') {
        return $false
    }
    if ($normalized -match '\.(pyc|pyo|eml|pdf|log|zip)$') {
        return $false
    }
    if (
        $normalized -match '^config/.+\.toml$' -and
        $normalized -notmatch '\.example\.toml$'
    ) {
        return $false
    }
    return $true
}

$tempRoot = [System.IO.Path]::GetTempPath().TrimEnd('\')
$staging = Join-Path $tempRoot (
    'pruefversand-home-package-' + [Guid]::NewGuid().ToString('N')
)
if (-not $staging.StartsWith(
    $tempRoot + '\',
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw 'Chemin temporaire du paquet invalide.'
}

try {
    New-Item -ItemType Directory -Path $staging | Out-Null
    $sourceFiles = @()
    foreach ($relativePath in $rootFiles) {
        $sourceFiles += Get-Item -LiteralPath (Join-Path $projectRoot $relativePath)
    }
    foreach ($relativeDirectory in $sourceDirectories) {
        $sourceFiles += Get-ChildItem -File -Recurse -LiteralPath (
            Join-Path $projectRoot $relativeDirectory
        )
    }

    foreach ($sourceFile in $sourceFiles) {
        $relativePath = $sourceFile.FullName.Substring(
            $projectRoot.Length + 1
        ).Replace('\', '/')
        if (-not (Test-AllowedPackagePath $relativePath)) {
            continue
        }
        $destination = Join-Path $staging $relativePath.Replace('/', '\')
        $destinationDirectory = [System.IO.Path]::GetDirectoryName($destination)
        New-Item -ItemType Directory -Force -Path $destinationDirectory |
            Out-Null
        Copy-Item -LiteralPath $sourceFile.FullName -Destination $destination
    }

    $manifestLines = Get-ChildItem -File -Recurse -LiteralPath $staging |
        ForEach-Object {
            $relativePath = $_.FullName.Substring(
                $staging.Length + 1
            ).Replace('\', '/')
            $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
            '{0}  {1}' -f $hash.ToLowerInvariant(), $relativePath
        } |
        Sort-Object
    $manifestPath = Join-Path $staging 'PACKAGE_MANIFEST.sha256'
    $manifestLines | Set-Content -Encoding utf8 -LiteralPath $manifestPath

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $staging,
        $outputFullPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )

    $archive = [System.IO.Compression.ZipFile]::OpenRead($outputFullPath)
    try {
        $entries = @{}
        foreach ($entry in $archive.Entries) {
            $normalizedEntryName = $entry.FullName.Replace('\', '/')
            $entries[$normalizedEntryName] = $entry
            if (-not (Test-AllowedPackagePath $normalizedEntryName)) {
                throw (
                    'Entree interdite dans le paquet : {0}' -f
                    $normalizedEntryName
                )
            }
        }
        foreach ($required in @(
            'PACKAGE_MANIFEST.sha256',
            'config/home.example.toml',
            'sample_data/nas_mock/README.md',
            'src/pruefversand/home_demo.py'
        )) {
            if (-not $entries.ContainsKey($required)) {
                throw ('Entree requise absente du paquet : {0}' -f $required)
            }
        }
        $reader = New-Object System.IO.StreamReader(
            $entries['PACKAGE_MANIFEST.sha256'].Open()
        )
        try {
            $manifest = $reader.ReadToEnd() -split '[\r\n]+' |
                Where-Object { $_ }
        }
        finally {
            $reader.Dispose()
        }
        foreach ($line in $manifest) {
            if ($line -notmatch '^([0-9a-f]{64})  (.+)$') {
                throw ('Ligne de manifeste invalide : {0}' -f $line)
            }
            $expectedHash = $Matches[1]
            $entryName = $Matches[2]
            if (-not $entries.ContainsKey($entryName)) {
                throw ('Fichier du manifeste absent : {0}' -f $entryName)
            }
            $sha256 = [System.Security.Cryptography.SHA256]::Create()
            $stream = $entries[$entryName].Open()
            try {
                $actualHash = [System.BitConverter]::ToString(
                    $sha256.ComputeHash($stream)
                ).Replace('-', '').ToLowerInvariant()
            }
            finally {
                $stream.Dispose()
                $sha256.Dispose()
            }
            if ($actualHash -ne $expectedHash) {
                throw ('SHA-256 invalide dans le paquet : {0}' -f $entryName)
            }
        }
        $entryCount = $archive.Entries.Count
        $verifiedFileCount = $manifest.Count
    }
    finally {
        $archive.Dispose()
    }

    $packageHash = (
        Get-FileHash -Algorithm SHA256 -LiteralPath $outputFullPath
    ).Hash.ToLowerInvariant()
    ('{0}  {1}' -f $packageHash, [System.IO.Path]::GetFileName(
        $outputFullPath
    )) | Set-Content -Encoding ascii -LiteralPath $checksumPath
    [ordered]@{
        status = 'HOME_PACKAGE_CREATED_AND_VERIFIED'
        path = $outputFullPath
        sha256 = $packageHash
        checksum_path = $checksumPath
        entry_count = $entryCount
        verified_file_count = $verifiedFileCount
    } | ConvertTo-Json
}
catch {
    if (Test-Path -LiteralPath $outputFullPath) {
        Remove-Item -Force -LiteralPath $outputFullPath
    }
    if (Test-Path -LiteralPath $checksumPath) {
        Remove-Item -Force -LiteralPath $checksumPath
    }
    throw
}
finally {
    if (
        (Test-Path -LiteralPath $staging) -and
        $staging.StartsWith(
            $tempRoot + '\pruefversand-home-package-',
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        Remove-Item -Recurse -Force -LiteralPath $staging
    }
}
