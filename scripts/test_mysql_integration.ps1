[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$image = 'mysql:8.4'
$database = 'pruefversand_test'
$user = 'pruefversand_test'
$suffix = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$containerName = 'pruefversand-mysql84-test-{0}' -f $suffix
$rootPassword = [Guid]::NewGuid().ToString('N')
$appPassword = [Guid]::NewGuid().ToString('N')
$containerStarted = $false

try {
    docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) {
        throw ('Image locale {0} absente; aucun telechargement automatique.' -f $image)
    }

    $containerId = docker run --detach --rm `
        --name $containerName `
        --env ('MYSQL_ROOT_PASSWORD={0}' -f $rootPassword) `
        --env ('MYSQL_DATABASE={0}' -f $database) `
        --env ('MYSQL_USER={0}' -f $user) `
        --env ('MYSQL_PASSWORD={0}' -f $appPassword) `
        --publish '127.0.0.1::3306' `
        $image
    if ($LASTEXITCODE -ne 0) {
        throw 'Impossible de demarrer le conteneur MySQL 8.4 temporaire.'
    }
    $containerStarted = $true

    $portMapping = docker port $containerName '3306/tcp'
    if ($LASTEXITCODE -ne 0 -or -not $portMapping) {
        throw 'Impossible de lire le port temporaire MySQL.'
    }
    $mysqlPort = [int](($portMapping | Select-Object -First 1) -split ':')[-1]

    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
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
        throw 'MySQL 8.4 temporaire ne repond pas.'
    }

    $env:PYTHONPATH = 'src'
    $env:PRUEFVERSAND_MYSQL_TEST = '1'
    $env:PRUEFVERSAND_MYSQL_TEST_HOST = '127.0.0.1'
    $env:PRUEFVERSAND_MYSQL_TEST_PORT = [string]$mysqlPort
    $env:PRUEFVERSAND_MYSQL_TEST_DATABASE = $database
    $env:PRUEFVERSAND_MYSQL_TEST_USER = $user
    $env:PRUEFVERSAND_MYSQL_TEST_PASSWORD = $appPassword

    python -m unittest discover -s tests_mysql -v
    if ($LASTEXITCODE -ne 0) {
        throw 'Les tests integration MySQL ont echoue.'
    }
}
finally {
    Remove-Item Env:PRUEFVERSAND_MYSQL_TEST_PASSWORD `
        -ErrorAction SilentlyContinue
    Remove-Item Env:PRUEFVERSAND_MYSQL_TEST `
        -ErrorAction SilentlyContinue
    if ($containerStarted) {
        docker stop --time 5 $containerName *> $null
    }
}
