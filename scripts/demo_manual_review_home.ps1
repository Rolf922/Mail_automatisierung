[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$image = 'mysql:8.4'
$database = 'pruefversand'
$user = 'pruefversand_app'
$suffix = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$containerName = 'pruefversand-manual-review-home-{0}' -f $suffix
$rootPassword = [Guid]::NewGuid().ToString('N')
$appPassword = [Guid]::NewGuid().ToString('N')
$containerStarted = $false

try {
    $listener = Get-NetTCPConnection -LocalPort 3307 -State Listen `
        -ErrorAction SilentlyContinue
    if ($listener) {
        throw 'Le port 3307 est deja occupe; aucun service ne sera modifie.'
    }

    docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) {
        throw (
            'Image locale {0} absente; aucun telechargement automatique.' `
                -f $image
        )
    }

    docker run --detach --rm `
        --name $containerName `
        --env ('MYSQL_ROOT_PASSWORD={0}' -f $rootPassword) `
        --env ('MYSQL_DATABASE={0}' -f $database) `
        --env ('MYSQL_USER={0}' -f $user) `
        --env ('MYSQL_PASSWORD={0}' -f $appPassword) `
        --publish '127.0.0.1:3307:3306' `
        $image *> $null
    if ($LASTEXITCODE -ne 0) {
        throw 'Impossible de demarrer MySQL Docker sur le port 3307.'
    }
    $containerStarted = $true

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        docker exec $containerName sh -c `
            'MYSQL_PWD=$MYSQL_ROOT_PASSWORD mysqladmin ping --host 127.0.0.1 --user root --silent' `
            *> $null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        throw 'MySQL Docker HOME ne repond pas.'
    }

    $env:PYTHONPATH = 'src'
    $env:MYSQL_PASSWORD = $appPassword
    $emlCountBefore = @(
        Get-ChildItem -LiteralPath 'var/mock_mail' -Filter '*.eml' `
            -File -ErrorAction SilentlyContinue
    ).Count
    python -m pruefversand `
        --config config/home.example.toml `
        demo-manual-review-home
    if ($LASTEXITCODE -ne 0) {
        throw 'La commande demo-manual-review-home a echoue.'
    }
    $emlCountAfter = @(
        Get-ChildItem -LiteralPath 'var/mock_mail' -Filter '*.eml' `
            -File -ErrorAction SilentlyContinue
    ).Count
    if ($emlCountAfter -ne $emlCountBefore) {
        throw 'La demonstration a cree un fichier .eml.'
    }
    Write-Output (
        'eml_count_before={0} after={1}' -f `
            $emlCountBefore,
            $emlCountAfter
    )
}
finally {
    Remove-Item Env:MYSQL_PASSWORD -ErrorAction SilentlyContinue
    if ($containerStarted) {
        docker stop --time 5 $containerName *> $null
    }
}
