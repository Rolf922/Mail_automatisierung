[CmdletBinding()]
param(
    [ValidateSet(
        'List',
        'VersandList',
        'VersandAccept',
        'VersandRetry',
        'VersandKeep',
        'Assign',
        'Ignore'
    )]
    [string]$Action = 'List',
    [string]$Filename = '',
    [string]$Auftragsnummer = '',
    [string]$Reason = '',
    [int]$VersandId = 0,
    [string]$ContainerName = 'pruefversand-weekly-home-active'
)

$ErrorActionPreference = 'Stop'
$database = 'pruefversand'
$user = 'pruefversand_app'
$startedByScript = $false
$expectedEmlDelta = 0

function Get-DatabaseFingerprint {
    param(
        [string]$Container,
        [string]$Password
    )
    $query = @'
SELECT CONCAT_WS('|',
    (SELECT COUNT(*) FROM csv_import),
    (SELECT COUNT(*) FROM scan_observation),
    (SELECT COUNT(*) FROM protokoll),
    (SELECT COUNT(*) FROM versand),
    (SELECT COUNT(*) FROM versand_protokoll),
    (SELECT COUNT(*) FROM manual_review_decision),
    (SELECT COUNT(*) FROM versand_review_decision),
    (SELECT COUNT(*) FROM audit_event),
    (SELECT COALESCE(MAX(updated_at), '') FROM protokoll),
    (SELECT COALESCE(MAX(updated_at), '') FROM versand)
)
'@
    return @(
        docker exec `
            --env ('MYSQL_PWD={0}' -f $Password) `
            $Container `
            mysql `
            --batch `
            --skip-column-names `
            ('-u{0}' -f $user) `
            ('-D{0}' -f $database) `
            --execute $query
    ) -join ''
}

try {
    $versandActions = @(
        'VersandAccept',
        'VersandRetry',
        'VersandKeep'
    )
    if (
        $Action -ne 'List' `
        -and $Action -ne 'VersandList'
    ) {
        if ([string]::IsNullOrWhiteSpace($Reason)) {
            throw 'Une raison est obligatoire pour toute decision.'
        }
    }
    if ($Action -eq 'Assign' -or $Action -eq 'Ignore') {
        if ([string]::IsNullOrWhiteSpace($Filename)) {
            throw 'Un nom de PDF synthetique est obligatoire.'
        }
    }
    if ($Action -in $versandActions -and $VersandId -le 0) {
        throw 'Un Versand ID positif est obligatoire.'
    }
    if (
        $Action -eq 'Assign' `
        -and [string]::IsNullOrWhiteSpace($Auftragsnummer)
    ) {
        throw 'Une Auftragsnummer est obligatoire pour Assign.'
    }

    $allContainers = @(
        docker ps -a --format '{{.Names}}'
    )
    if ($ContainerName -notin $allContainers) {
        throw (
            'Conteneur HOME conserve introuvable : {0}. ' +
            'Cette commande de consultation ne cree aucune base.' `
                -f $ContainerName
        )
    }
    $image = docker inspect --format '{{.Config.Image}}' $ContainerName
    if ($LASTEXITCODE -ne 0 -or $image -ne 'mysql:8.4') {
        throw 'Le conteneur HOME doit utiliser l image locale mysql:8.4.'
    }

    $runningContainers = @(
        docker ps --format '{{.Names}}'
    )
    if ($ContainerName -notin $runningContainers) {
        $listener = Get-NetTCPConnection `
            -LocalPort 3307 `
            -State Listen `
            -ErrorAction SilentlyContinue
        if ($listener) {
            throw 'Le port 3307 est occupe; aucun service ne sera modifie.'
        }
        docker start $ContainerName *> $null
        if ($LASTEXITCODE -ne 0) {
            throw 'Impossible de demarrer le conteneur HOME conserve.'
        }
        $startedByScript = $true
    }

    $portMapping = @(docker port $ContainerName '3306/tcp')
    if (
        $LASTEXITCODE -ne 0 `
        -or $portMapping.Count -ne 1 `
        -or $portMapping[0] -ne '127.0.0.1:3307'
    ) {
        throw 'Le conteneur HOME doit publier MySQL sur 127.0.0.1:3307.'
    }

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        docker exec $ContainerName sh -c `
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

    $containerEnvironment = @(
        docker inspect `
            --format '{{range .Config.Env}}{{println .}}{{end}}' `
            $ContainerName
    )
    $passwordLine = @(
        $containerEnvironment `
            | Where-Object { $_ -clike 'MYSQL_PASSWORD=*' }
    )
    if ($passwordLine.Count -ne 1) {
        throw 'Identifiant HOME synthetique introuvable.'
    }
    if (
        'MYSQL_DATABASE=pruefversand' -notin $containerEnvironment `
        -or 'MYSQL_USER=pruefversand_app' -notin $containerEnvironment
    ) {
        throw 'Le conteneur ne correspond pas a la base HOME attendue.'
    }
    $appPassword = $passwordLine[0].Substring('MYSQL_PASSWORD='.Length)
    $env:PYTHONPATH = 'src'
    $env:MYSQL_PASSWORD = $appPassword
    $emlCountBefore = @(
        Get-ChildItem `
            -LiteralPath 'var/mock_mail' `
            -Filter '*.eml' `
            -File `
            -ErrorAction SilentlyContinue
    ).Count
    $readOnly = $Action -eq 'List' -or $Action -eq 'VersandList'
    if ($readOnly) {
        $fingerprintBefore = Get-DatabaseFingerprint `
            -Container $ContainerName `
            -Password $appPassword
    }

    if ($Action -eq 'VersandList') {
        python -m pruefversand `
            --config config/home.example.toml `
            versand-review list
        if ($LASTEXITCODE -ne 0) {
            throw 'versand-review list a echoue.'
        }
    }
    elseif ($Action -in $versandActions) {
        if ($Action -eq 'VersandAccept') {
            $resolutionCommand = 'confirm-accepted'
        }
        elseif ($Action -eq 'VersandRetry') {
            $resolutionCommand = 'retry-not-accepted'
        }
        else {
            $resolutionCommand = 'keep-unknown'
        }
        $resolutionLines = @(
            python -m pruefversand `
                --config config/home.example.toml `
                versand-review `
                $resolutionCommand `
                $VersandId `
                --reason $Reason
        )
        if ($LASTEXITCODE -ne 0) {
            throw ('versand-review {0} a echoue.' -f $resolutionCommand)
        }
        $resolutionText = $resolutionLines -join [Environment]::NewLine
        Write-Output $resolutionText
        $jsonStart = $resolutionText.IndexOf('{')
        if ($jsonStart -lt 0) {
            throw 'La commande de resolution n a retourne aucun JSON.'
        }
        $resolutionPayload = $resolutionText.Substring($jsonStart) `
            | ConvertFrom-Json
        if ($resolutionPayload.retry_performed -eq $true) {
            $expectedEmlDelta = 1
        }
    }
    elseif ($Action -eq 'List') {
        python -m pruefversand `
            --config config/home.example.toml `
            manual-review list
        if ($LASTEXITCODE -ne 0) {
            throw 'manual-review list a echoue.'
        }
    }
    else {
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
            $listPayload.items `
                | Where-Object { $_.name -eq $Filename }
        )
        if ($target.Count -ne 1) {
            throw (
                'PDF MANUAL_REVIEW introuvable ou ambigu : {0}' `
                    -f $Filename
            )
        }
        if ($Action -eq 'Assign') {
            python -m pruefversand `
                --config config/home.example.toml `
                manual-review assign `
                $target[0].id `
                $Auftragsnummer `
                --reason $Reason
        }
        else {
            python -m pruefversand `
                --config config/home.example.toml `
                manual-review ignore `
                $target[0].id `
                --reason $Reason
        }
        if ($LASTEXITCODE -ne 0) {
            throw ('manual-review {0} a echoue.' -f $Action.ToLower())
        }
    }

    $emlCountAfter = @(
        Get-ChildItem `
            -LiteralPath 'var/mock_mail' `
            -Filter '*.eml' `
            -File `
            -ErrorAction SilentlyContinue
    ).Count
    if (($emlCountAfter - $emlCountBefore) -ne $expectedEmlDelta) {
        throw 'La commande MANUAL_REVIEW a cree un fichier .eml.'
    }
    if ($readOnly) {
        $fingerprintAfter = Get-DatabaseFingerprint `
            -Container $ContainerName `
            -Password $appPassword
        if ($fingerprintAfter -ne $fingerprintBefore) {
            throw 'La consultation read-only a modifie la base HOME.'
        }
    }
    Write-Output (
        'read_only={0} eml_count_before={1} after={2}' -f `
            $readOnly.ToString().ToLowerInvariant(),
            $emlCountBefore,
            $emlCountAfter
    )
}
finally {
    Remove-Item Env:MYSQL_PASSWORD -ErrorAction SilentlyContinue
    if ($startedByScript) {
        docker stop --time 5 $ContainerName *> $null
    }
}
