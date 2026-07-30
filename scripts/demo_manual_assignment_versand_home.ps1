[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$image = 'mysql:8.4'
$database = 'pruefversand'
$user = 'pruefversand_app'
$suffix = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$containerName = 'pruefversand-manual-versand-home-{0}' -f $suffix
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
    $emlBefore = @(
        Get-ChildItem -LiteralPath 'var/mock_mail' -Filter '*.eml' `
            -File -ErrorAction SilentlyContinue
    )

    python -m pruefversand `
        --config config/home.example.toml `
        demo-manual-review-home
    if ($LASTEXITCODE -ne 0) {
        throw 'La preparation MANUAL_REVIEW synthetique a echoue.'
    }

    $listLines = @(
        python -m pruefversand `
            --config config/home.example.toml `
            manual-review list
    )
    if ($LASTEXITCODE -ne 0) {
        throw 'manual-review list a echoue.'
    }
    $listPayload = ($listLines -join [Environment]::NewLine) `
        | ConvertFrom-Json
    $target = @(
        $listPayload.items | Where-Object { $_.name -eq 'F0661.pdf' }
    )
    if ($target.Count -ne 1) {
        throw 'Le PDF synthetique F0661.pdf est introuvable.'
    }

    'OUI' | python -m pruefversand `
        --config config/home.example.toml `
        manual-review assign `
        $target[0].id `
        '00125083' `
        --reason 'Cycle Versand HOME synthetique'
    if ($LASTEXITCODE -ne 0) {
        throw 'manual-review assign a echoue.'
    }

    $firstLines = @(
        python -m pruefversand `
            --config config/home.example.toml `
            prepare-mock-versand
    )
    if ($LASTEXITCODE -ne 0) {
        throw 'La premiere preparation Versand mock a echoue.'
    }
    $firstText = $firstLines -join [Environment]::NewLine
    Write-Output $firstText
    $first = $firstText | ConvertFrom-Json
    if (
        $first.ready_document_count -ne 1 `
        -or $first.new_message_count -ne 1
    ) {
        throw 'Le premier Versand mock ne contient pas exactement un PDF.'
    }
    $invalidRecipients = @(
        $first.messages[0].recipients `
            | Where-Object { $_ -notlike '*@example.invalid' }
    )
    if ($invalidRecipients.Count -ne 0) {
        throw 'Un Empfaenger mock est hors de example.invalid.'
    }

    $emlAfterFirst = @(
        Get-ChildItem -LiteralPath 'var/mock_mail' -Filter '*.eml' `
            -File -ErrorAction SilentlyContinue
    )
    $created = @(
        $emlAfterFirst `
            | Where-Object { $_.FullName -notin $emlBefore.FullName }
    )
    if ($created.Count -ne 1) {
        throw 'Le premier passage doit creer exactement un fichier .eml.'
    }

    $secondLines = @(
        python -m pruefversand `
            --config config/home.example.toml `
            prepare-mock-versand
    )
    if ($LASTEXITCODE -ne 0) {
        throw 'La seconde preparation Versand mock a echoue.'
    }
    $secondText = $secondLines -join [Environment]::NewLine
    Write-Output $secondText
    $second = $secondText | ConvertFrom-Json
    if (
        $second.ready_document_count -ne 0 `
        -or $second.already_processed_count -ne 1 `
        -or $second.new_message_count -ne 0
    ) {
        throw 'Le second passage n a pas exclu le PDF deja traite.'
    }

    $emlAfterSecond = @(
        Get-ChildItem -LiteralPath 'var/mock_mail' -Filter '*.eml' `
            -File -ErrorAction SilentlyContinue
    )
    if ($emlAfterSecond.Count -ne $emlAfterFirst.Count) {
        throw 'Le second passage a cree un doublon .eml.'
    }
    Write-Output ('created_eml_path={0}' -f $created[0].FullName)
    Write-Output (
        'eml_count_before={0} after_first={1} after_second={2}' -f `
            $emlBefore.Count,
            $emlAfterFirst.Count,
            $emlAfterSecond.Count
    )
}
finally {
    Remove-Item Env:MYSQL_PASSWORD -ErrorAction SilentlyContinue
    if ($containerStarted) {
        docker stop --time 5 $containerName *> $null
    }
}
