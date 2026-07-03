$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = (Get-Location).Path
$txtDir = Join-Path $root '__txt'
$csvChron = Join-Path $root '__chronology.csv'
$jsonDeep = Join-Path $root '__deep_summary.json'

if (!(Test-Path $txtDir)) {
  Write-Host "Folder not found: $txtDir"
  exit 1
}

$culture = [System.Globalization.CultureInfo]::GetCultureInfo('en-GB')

function Parse-Filename {
  param([string]$name)
  $m=[regex]::Match($name,'^CD No\.\s*(?<no>\d+)\s+dated\s+(?<date>\d{1,2}\.\d{1,2}\.\s*\d{2})(?:\s+(?<title>.*))?$', 'IgnoreCase')
  if($m.Success){
    $cd=[int]$m.Groups['no'].Value
    $dateRaw=($m.Groups['date'].Value -replace '\s','')
    $title=($m.Groups['title'].Value).Trim()
    $dateIso=$null
    $dm=[regex]::Match($dateRaw,'^(?<d>\d{1,2})\.(?<m>\d{1,2})\.(?<y>\d{2})$')
    if($dm.Success){
      $d=[int]$dm.Groups['d'].Value; $mm=[int]$dm.Groups['m'].Value; $yy=[int]$dm.Groups['y'].Value
      $yyyy = 2000 + $yy
      $dateIso = ('{0:D4}-{1:D2}-{2:D2}' -f $yyyy, $mm, $d)
    }
    return [pscustomobject]@{ CDNo=$cd; Date=$dateIso; Title=$title }
  } else {
    return [pscustomobject]@{ CDNo=$null; Date=$null; Title=$name }
  }
}

function Extract-Sections {
  param([string]$text)
  $secs=@()
  foreach($m in ([regex]::Matches($text,'\b([1-9][0-9]{1,2}[A-Z]?)\s*IPC\b', 'IgnoreCase'))){ $secs += ($m.Groups[1].Value.ToUpper()+' IPC') }
  foreach($m in ([regex]::Matches($text,'\b([1-9][0-9]{1,2}[A-Z]?)\s*CrPC\b', 'IgnoreCase'))){ $secs += ($m.Groups[1].Value.ToUpper()+' CrPC') }
  foreach($m in ([regex]::Matches($text,'\bIT\s*Act\b', 'IgnoreCase'))){ $secs += 'IT Act' }
  return ($secs | Select-Object -Unique)
}

function Extract-Names {
  param([string]$text)
  $names=@()
  foreach($m in ([regex]::Matches($text,'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b'))){
    $n=$m.Groups[1].Value.Trim()
    if($n.Length -ge 4 -and $n -notmatch '^(Police|Station|Court|Delhi|FIR|Bank|Account|Mobile|Email|Address|Officer|Inspector|Sub|Head|Constable|HC|SI|ASI|SHO|IO|U/S|Under|Section|Date|Time|Place|No|CD|Accused|Complainant|Witness)$'){
      $names += $n
    }
  }
  return ($names | Group-Object | Sort-Object Count -Descending | Select-Object -First 10 -ExpandProperty Name)
}

function Extract-Amounts {
  param([string]$text)
  $amts=@()
  $patterns = @('\bRs\.?\s?\d[\d,\.]*','\bINR\s?\d[\d,\.]*','\b\d+\s*(?:lakh|lac|crore)s?\b')
  foreach($p in $patterns){ foreach($m in [regex]::Matches($text,$p,'IgnoreCase')){ $amts += $m.Value } }
  return ($amts | Select-Object -Unique | Select-Object -First 20)
}

function Extract-Phones {
  param([string]$text)
  $phones=@()
  foreach($m in [regex]::Matches($text,'\b[6-9]\d{9}\b')){ $phones += $m.Value }
  return ($phones | Select-Object -Unique | Select-Object -First 20)
}

function Extract-Emails {
  param([string]$text)
  $emails=@()
  foreach($m in [regex]::Matches($text,'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')){ $emails += $m.Value }
  return ($emails | Select-Object -Unique | Select-Object -First 20)
}

function Extract-Accounts {
  param([string]$text)
  $accs=@()
  foreach($m in [regex]::Matches($text,'\b\d{9,18}\b')){ $accs += $m.Value }
  return ($accs | Select-Object -Unique | Select-Object -First 20)
}

function Extract-IFSC {
  param([string]$text)
  $ifs=@()
  foreach($m in [regex]::Matches($text,'\b[A-Z]{4}0[0-9A-Z]{6}\b')){ $ifs += $m.Value }
  return ($ifs | Select-Object -Unique | Select-Object -First 20)
}

function Extract-UPI {
  param([string]$text)
  $upis=@()
  foreach($m in [regex]::Matches($text,'\b[\w\.-]+@[\w-]+\b')){ $upis += $m.Value }
  return ($upis | Select-Object -Unique | Select-Object -First 20)
}

$knownBanks = @('State Bank of India','SBI','HDFC','ICICI','Axis','Kotak','Yes Bank','Punjab National Bank','PNB','Bank of Baroda','Canara','IDFC','IndusInd','IDBI','RBL','AU','UCO','Union Bank','Central Bank','Indian Bank','Federal Bank','City Union','Bandhan','Paytm','PhonePe','Google Pay')
function Detect-Banks {
  param([string]$text)
  $banks=@()
  foreach($b in $knownBanks){ if($text -match [regex]::Escape($b)){ $banks += $b } }
  foreach($m in [regex]::Matches($text,'\b([A-Z][A-Za-z]+)\s+Bank\b')){ $banks += ($m.Groups[1].Value + ' Bank') }
  return ($banks | Select-Object -Unique | Select-Object -First 10)
}

$keywordTags = @{
  'arrest'='Arrest'; 'bail'='Bail'; 'remand'='Remand'; 'judicial custody'='Judicial Custody'; 'j.c'='Judicial Custody'; 'jc'='Judicial Custody'; 'police custody'='Police Custody'; 'p.c'='Police Custody'; 'pc'='Police Custody'; 'notice'='Notice'; 'seiz'='Seizure'; 'search'='Search'; 'interrogation'='Interrogation'; 'disclosure'='Disclosure'; 'recover'='Recovery'; 'freeze'='Freeze'; 'defreeze'='Defreeze'; 'bank'='Bank'; 'kyc'='KYC'; 'gmail'='Gmail'; 'email'='Email'; 'cdr'='CDR'; 'transit'='Transit'; 'out station'='Outstation'; 'outstation'='Outstation'; 'bound down'='Bound Down'; 'challan'='Challan'; 'charge sheet'='Charge Sheet'
}
function Detect-Tags {
  param([string]$text, [string]$title)
  $tags=@()
  $hay = ($title + ' ' + $text)
  $hayLower = $hay.ToLowerInvariant()
  foreach($k in $keywordTags.Keys){ if($hayLower -like ('*' + $k + '*')){ $tags += $keywordTags[$k] } }
  return ($tags | Select-Object -Unique)
}

$rows=@()
$events=@()
$globalCounts = [ordered]@{ Documents = 0 }
$tagCounts=@{}
$sectionCounts=@{}
$nameCounts=@{}
$bankCounts=@{}

Get-ChildItem -Path $txtDir -Filter '*.txt' | ForEach-Object {
  $bn = $_.BaseName
  $meta = Parse-Filename $bn
  $text = Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue
  if(-not $text){ $text = '' }
  $sections = Extract-Sections $text
  foreach($s in $sections){ if($sectionCounts.ContainsKey($s)){ $sectionCounts[$s]++ } else { $sectionCounts[$s]=1 } }
  $names = Extract-Names $text
  foreach($n in $names){ if($nameCounts.ContainsKey($n)){ $nameCounts[$n]++ } else { $nameCounts[$n]=1 } }
  $amounts = Extract-Amounts $text
  $phones = Extract-Phones $text
  $emails = Extract-Emails $text
  $accounts = Extract-Accounts $text
  $ifsccodes = Extract-IFSC $text
  $upis = Extract-UPI $text
  $banks = Detect-Banks $text
  foreach($b in $banks){ if($bankCounts.ContainsKey($b)){ $bankCounts[$b]++ } else { $bankCounts[$b]=1 } }
  $tags = Detect-Tags $text $meta.Title
  foreach($t in $tags){ if($tagCounts.ContainsKey($t)){ $tagCounts[$t]++ } else { $tagCounts[$t]=1 } }

  $dateIso = $meta.Date
  $snippet = ($text -split "\r?\n")[0]
  $rows += [pscustomobject]@{
    File = $_.Name
    CDNo = $meta.CDNo
    Date = $dateIso
    Title = $meta.Title
    Tags = ($tags -join '; ')
    Sections = ($sections -join '; ')
    Names = ($names -join '; ')
    Banks = ($banks -join '; ')
    Amounts = ($amounts -join '; ')
    Phones = ($phones -join '; ')
    Emails = ($emails -join '; ')
    Accounts = ($accounts -join '; ')
    IFSC = ($ifsccodes -join '; ')
    UPI = ($upis -join '; ')
    Snippet = $snippet
  }
  $events += [pscustomobject]@{ CDNo=$meta.CDNo; Date=$dateIso; Title=$meta.Title; Tags=$tags; Sections=$sections; Names=$names; Banks=$banks }
  $globalCounts.Documents++
}

$rows | Sort-Object {[int]($_.CDNo)} | Export-Csv -NoTypeInformation -Path $csvChron -Encoding UTF8

$topSections = $sectionCounts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 20
$topNamesList = $nameCounts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 25
$topBanks = $bankCounts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 20
$topTags = $tagCounts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 20

$chron = $events | Sort-Object @{Expression='Date';Descending=$false}, @{Expression='CDNo';Descending=$false} | ForEach-Object { [pscustomobject]@{ CDNo=$_.CDNo; Date=$_.Date; Title=$_.Title; Tags=($_.Tags -join ', '); Sections=($_.Sections -join ', '); Names=($_.Names -join ', '); Banks=($_.Banks -join ', ') } }

$summary = [pscustomobject]@{
  Counts = $globalCounts
  TopSections = ($topSections | ForEach-Object { [pscustomobject]@{ Section=$_.Key; Count=$_.Value } })
  TopNames = ($topNamesList | ForEach-Object { [pscustomobject]@{ Name=$_.Key; Mentions=$_.Value } })
  TopBanks = ($topBanks | ForEach-Object { [pscustomobject]@{ Bank=$_.Key; Mentions=$_.Value } })
  TopTags = ($topTags | ForEach-Object { [pscustomobject]@{ Tag=$_.Key; Count=$_.Value } })
  Chronology = $chron
}

$summary | ConvertTo-Json -Depth 8 | Set-Content -Path $jsonDeep -Encoding UTF8

Write-Host "Wrote: $csvChron"
Write-Host "Wrote: $jsonDeep" 