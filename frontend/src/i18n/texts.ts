import i18next from 'i18next'

/**
 * Texts that arrive from the server in English and are translated by their
 * English wording: adapter field labels and help, widget names and
 * descriptions, and the labels of values, chips, rows and actions.
 *
 * English needs no table. Every other language ships one JSON file with two
 * sections; an unknown text is shown as it came, so a new adapter never
 * breaks a page, it only stays English until the table knows it.
 */
export type TextSection = 'adapter' | 'labels'
export type TextBundle = Record<TextSection, Record<string, string>>

const bundles: Record<string, TextBundle> = {}

export function registerTexts(language: string, bundle: TextBundle): void {
  bundles[language] = bundle
}

/** Phrases with numbers or sizes inside: the number stays, the words change. */
const PATTERNS: Record<string, [RegExp, string][]> = {
  de: [
    [/^([\d.,]+ [KMGTP]?B) of ([\d.,]+ [KMGTP]?B)$/, '$1 von $2'],
    [/^(\d+) problem\(s\)$/, '$1 Problem(e)'],
    [/^(\d+) queries, (\d+) grabs$/, '$1 Abfragen, $2 Treffer'],
    [/^(\d+) min left$/, 'noch $1 min'],
    [/^mem (\d+)%$/, 'RAM $1%'],
    [/^disk (\d+)%$/, 'Platte $1%'],
    [/^(\d+) error finding\(s\), (\d+) warning\(s\)$/, '$1 Fehler-Befund(e), $2 Warnung(en)'],
    [/^(\d+) device\(s\) offline$/, '$1 Gerät(e) offline'],
    [/^(\d+) devices online$/, '$1 Geräte online'],
    [/^(\d+) clients$/, '$1 Clients'],
    [/^memory (\d+)%$/, 'RAM $1%'],
    [/^restarted (\d+) min ago$/, 'vor $1 min neu gestartet'],
    [/^on 1 access point$/, 'auf 1 Access Point'],
    [/^1 gateway$/, '1 Gateway'],
    [/^(\d+) gateways$/, '$1 Gateways'],
    [/^1 switch$/, '1 Switch'],
    [/^(\d+) switches$/, '$1 Switches'],
    [/^1 access point$/, '1 Access Point'],
    [/^(\d+) access points$/, '$1 Access Points'],
    [/^(\d+) wireless$/, '$1 WLAN'],
    [/^(\d+) wired$/, '$1 Kabel'],
    [/^(\d+) update\(s\) available$/, '$1 Update(s) verfügbar'],
    [/^(\d+) play\(s\)$/, '$1 Wiedergabe(n)'],
    [/^battery (\d+)%$/, 'Akku $1 %'],
    [/^(\d+) cameras$/, '$1 Kameras'],
    [/^Channel (\d+)$/, 'Kanal $1'],
    [/^(\d+) failed sign-ins in 24 h$/, '$1 gescheiterte Anmeldungen in 24 h'],
    [/^(\d+) error\(s\) in 24 h$/, '$1 Fehler in 24 h'],
    [/^(\d+) errors$/, '$1 Fehler'],
    [/^([\d.,]+ [KMGTP]?i?B) free$/, '$1 frei'],
    [/^Account (\d+)$/, 'Konto $1'],
    [/^Device (\d+)$/, 'Gerät $1'],
    [/^Update (.+) available$/, 'Update $1 verfügbar'],
    [/^(.+) is being scanned$/, '$1 wird gerade gescannt'],
    [/^(.+) last scanned (\d+) days ago$/, '$1 zuletzt vor $2 Tagen gescannt'],
    [/^Plex uses (\d+)% CPU$/, 'Plex nutzt $1 % CPU'],
    [/^The host is at (\d+)% CPU$/, 'Der Host liegt bei $1 % CPU'],
    [/^(\d+) video transcodes running$/, '$1 Video-Transcodes laufen'],
    [/^(\d+) libraries$/, '$1 Bibliotheken'],
    [/^on (\d+) access points$/, 'auf $1 Access Points'],
    [/^Up (\d+) days$/, 'Läuft seit $1 Tagen'],
    [/^Up (\d+) hours$/, 'Läuft seit $1 Stunden'],
    [/^Up (\d+) minutes$/, 'Läuft seit $1 Minuten'],
    [/^Up (\d+) seconds$/, 'Läuft seit $1 Sekunden'],
    [/^Up About an hour$/, 'Läuft seit etwa einer Stunde'],
    [/^Up About a minute$/, 'Läuft seit etwa einer Minute'],
    [/^(\d+) days ago$/, 'vor $1 Tagen'],
    [/^(\d+) weeks ago$/, 'vor $1 Wochen'],
    [/^(\d+) months ago$/, 'vor $1 Monaten'],
    [/^Exited \((\d+)\) (\d+) months ago$/, 'Beendet ($1) vor $2 Monaten'],
    [/^Exited \((\d+)\) (\d+) weeks ago$/, 'Beendet ($1) vor $2 Wochen'],
    [/^Exited \((\d+)\) (\d+) days ago$/, 'Beendet ($1) vor $2 Tagen'],
    [/^Exited \((\d+)\) (\d+) hours ago$/, 'Beendet ($1) vor $2 Stunden'],
    [/^Exited \((\d+)\) (\d+) minutes ago$/, 'Beendet ($1) vor $2 Minuten'],
    [/^Up (\d+) weeks$/, 'Läuft seit $1 Wochen'],
    [/^Up (\d+) months$/, 'Läuft seit $1 Monaten'],
    [/^(\d+) hours ago$/, 'vor $1 Stunden'],
    [/^(\d+) minutes ago$/, 'vor $1 Minuten'],
    [/^finished (.+)$/, 'fertig $1'],
    [/^last (.+)$/, 'zuletzt $1'],
    [/^renewed (\d+) days ago$/, 'vor $1 Tagen erneuert'],
  ],
  it: [
    [/^([\d.,]+ [KMGTP]?B) of ([\d.,]+ [KMGTP]?B)$/, '$1 di $2'],
    [/^(\d+) problem\(s\)$/, '$1 problema/i'],
    [/^(\d+) queries, (\d+) grabs$/, '$1 query, $2 grab'],
    [/^(\d+) min left$/, 'mancano $1 min'],
    [/^mem (\d+)%$/, 'RAM $1%'],
    [/^disk (\d+)%$/, 'disco $1%'],
    [/^(\d+) error finding\(s\), (\d+) warning\(s\)$/, '$1 errore/i, $2 avviso/i'],
    [/^(\d+) device\(s\) offline$/, '$1 dispositivo/i offline'],
    [/^(\d+) devices online$/, '$1 dispositivi online'],
    [/^(\d+) clients$/, '$1 client'],
    [/^memory (\d+)%$/, 'RAM $1%'],
    [/^restarted (\d+) min ago$/, 'riavviato $1 min fa'],
    [/^on 1 access point$/, 'su 1 access point'],
    [/^1 gateway$/, '1 gateway'],
    [/^(\d+) gateways$/, '$1 gateway'],
    [/^1 switch$/, '1 switch'],
    [/^(\d+) switches$/, '$1 switch'],
    [/^1 access point$/, '1 access point'],
    [/^(\d+) access points$/, '$1 access point'],
    [/^(\d+) wireless$/, '$1 wireless'],
    [/^(\d+) wired$/, '$1 cablati'],
    [/^(\d+) update\(s\) available$/, '$1 aggiornamento/i disponibile/i'],
    [/^(\d+) play\(s\)$/, '$1 riproduzione/i'],
    [/^battery (\d+)%$/, 'batteria $1%'],
    [/^(\d+) cameras$/, '$1 telecamere'],
    [/^Channel (\d+)$/, 'Canale $1'],
    [/^(\d+) failed sign-ins in 24 h$/, '$1 accessi falliti in 24 h'],
    [/^(\d+) error\(s\) in 24 h$/, '$1 errore/i in 24 h'],
    [/^(\d+) errors$/, '$1 errori'],
    [/^([\d.,]+ [KMGTP]?i?B) free$/, '$1 liberi'],
    [/^Account (\d+)$/, 'Account $1'],
    [/^Device (\d+)$/, 'Dispositivo $1'],
    [/^Update (.+) available$/, 'Aggiornamento $1 disponibile'],
    [/^(.+) is being scanned$/, '$1 in scansione'],
    [/^(.+) last scanned (\d+) days ago$/, '$1 scansionata $2 giorni fa'],
    [/^Plex uses (\d+)% CPU$/, 'Plex usa il $1% della CPU'],
    [/^The host is at (\d+)% CPU$/, "L'host è al $1% di CPU"],
    [/^(\d+) video transcodes running$/, '$1 transcodifiche video in corso'],
    [/^(\d+) libraries$/, '$1 librerie'],
    [/^on (\d+) access points$/, 'su $1 access point'],
    [/^Up (\d+) days$/, 'Attivo da $1 giorni'],
    [/^Up (\d+) hours$/, 'Attivo da $1 ore'],
    [/^Up (\d+) minutes$/, 'Attivo da $1 minuti'],
    [/^Up (\d+) seconds$/, 'Attivo da $1 secondi'],
    [/^Up About an hour$/, "Attivo da circa un'ora"],
    [/^Up About a minute$/, 'Attivo da circa un minuto'],
    [/^(\d+) days ago$/, '$1 giorni fa'],
    [/^(\d+) weeks ago$/, '$1 settimane fa'],
    [/^(\d+) months ago$/, '$1 mesi fa'],
    [/^Exited \((\d+)\) (\d+) months ago$/, 'Uscito ($1) $2 mesi fa'],
    [/^Exited \((\d+)\) (\d+) weeks ago$/, 'Uscito ($1) $2 settimane fa'],
    [/^Exited \((\d+)\) (\d+) days ago$/, 'Uscito ($1) $2 giorni fa'],
    [/^Exited \((\d+)\) (\d+) hours ago$/, 'Uscito ($1) $2 ore fa'],
    [/^Exited \((\d+)\) (\d+) minutes ago$/, 'Uscito ($1) $2 minuti fa'],
    [/^Up (\d+) weeks$/, 'Attivo da $1 settimane'],
    [/^Up (\d+) months$/, 'Attivo da $1 mesi'],
    [/^(\d+) hours ago$/, '$1 ore fa'],
    [/^(\d+) minutes ago$/, '$1 minuti fa'],
    [/^finished (.+)$/, 'finito $1'],
    [/^last (.+)$/, 'ultimo $1'],
    [/^renewed (\d+) days ago$/, 'rinnovato $1 giorni fa'],
  ],
}

function language(): string {
  return (i18next.language || 'en').split('-')[0]
}

/** Translate one server text; unknown texts come back unchanged. */
export function translateText(section: TextSection, text: string | number | null | undefined): string {
  if (text === null || text === undefined || text === '') return ''
  const source = String(text)
  const code = language()
  if (code === 'en') return source
  const table = bundles[code]?.[section]
  if (table && Object.prototype.hasOwnProperty.call(table, source)) return table[source]
  if (section === 'labels') {
    // The same English word often names a widget and a value: Queue, Streams, Devices.
    const shared = bundles[code]?.adapter
    if (shared && Object.prototype.hasOwnProperty.call(shared, source)) return shared[source]
    if (source.includes(' · ')) {
      return source
        .split(' · ')
        .map((part) => translateText('labels', part))
        .join(' · ')
    }
    for (const [pattern, replacement] of PATTERNS[code] ?? []) {
      if (pattern.test(source)) return source.replace(pattern, replacement)
    }
  }
  return source
}

/** Field labels, help texts, widget names and descriptions of the adapters. */
export const tAdapter = (text: string | null | undefined): string => translateText('adapter', text)

/** Labels of values, chips, rows and actions on the cards. */
export const tLabel = (text: string | number | null | undefined): string => translateText('labels', text)
