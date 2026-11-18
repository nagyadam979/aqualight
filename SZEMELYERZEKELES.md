# Személyérzékelés és behatolásjelzés – ötletterv

> Állapot: **ötlet / tervezés**, nincs megvalósítva. A firmware-t ez a dokumentum nem érinti.

## Cél

Ha a szoba tulajdonosa nincs otthon, és egy **harmadik fél** (nem a két szokásos lakótárs) a szobája felé indul, a szobai világítás és a terrárium látható fénye kapcsoljon le, és menjen értesítés.

Három külön kérdést kell megválaszolni:

1. **Bejött-e valaki a lakásba?** – esemény
2. **Ki az?** – lakótárs (nincs teendő) vagy harmadik fél
3. **A szobám felé tart-e?** – szándék / irány

## Döntések

- **Adatkezelés:** a lakótársak tudnak a rendszerről, és önként regisztrálják magukat. Harmadik fél adata nem tárolódik: a képkocka csak RAM-ban él, és a döntés után eldobódik. Flash-ben csak a regisztrált személyek arc-embeddingje marad meg.
- **Azonosítás kamerával** történik (arcfelismerés), nem telefon- vagy MAC-követéssel. A Wi-Fi-t mozgásérzékelésre használjuk.
- **Éjszakai mód / IR nem kell**, sötétben nem érdekes a működés.
- **A terrárium fűtését és UVB-jét a riasztás soha nem kapcsolja le**, csak a látható fényt.

## Felépítés

```
[Előszoba: ESP32-S3 kamera]          [Szobaajtó-zóna]              [Szoba: AquaLight vezérlők]
 mozgás-trigger (képkülönbség/CSI)    CSI a két AquaLight között     világítás le (fűtés/UVB nem)
 → arcdetektálás + felismerés         (vagy PIR/mmWave)              + értesítés
 → "idegen bent" / "lakótárs" /       → "mozgás a szobaajtónál"
   "tulajdonos hazaért"
                 └──────────────── ESP-NOW állapotüzenetek ────────────────┘
```

### Állapotgép

```
NEM ÉLES (tulajdonos otthon)
   │ tulajdonos elmegy (kézi élesítés vagy jelenlét-alapú)
   ▼
ÉLES ──mozgás az előszobában──► FELISMERÉS
                                  ├─ csak regisztrált lakótárs ──► nincs teendő
                                  ├─ tulajdonos felismerve ──────► NEM ÉLES
                                  ├─ bármely ismeretlen arc ─────► IDEGEN BENT (N percig)
                                  └─ nem látszott arc ───────────► BIZONYTALAN (csak értesítés)
IDEGEN BENT + mozgás a szobaajtónál ──► lámpák le + értesítés
```

A kamera két korábbi gyenge pontot is megold:
- **Lakótárs + vendég együtt:** ha bármelyik arc ismeretlen, „idegen bent” lesz az állapot. Telefonalapú azonosítással ez nem dönthető el.
- **A tulajdonos hazaér:** az arcfelismerés azonnal kikapcsolja az élesítést. Így nincs versenyhelyzet a telefon lassú Wi-Fi-csatlakozása miatt.

## Hardver

### Kamerás csomópont (előszoba, a bejárati ajtóra nézve)

- **ESP32-S3-N16R8-M + OV5640** (16 MB flash, 8 MB octal PSRAM), a kamera a lap foglalatában.
- A felismeréshez alacsony felbontás elég (QVGA, 320×240), az 5 MP-t nem használjuk ki.
- Az OV5640 autofókuszos változatánál a meghajtónak be kell töltenie az AF-firmware-t.
- Stabil 5 V-os táp kell, csúcsáramnál 400–500 mA. Az OV5640 folyamatos működésnél melegszik.
- Az octal PSRAM a GPIO35–37-et foglalja.

### Szobaajtó-zóna

- **Elsődlegesen Wi-Fi CSI** a két meglévő AquaLight (ESP32-C3) között, úgy elhelyezve, hogy a köztük lévő vonal keresztezze a szobaajtót.
- Ha a CSI nem elég megbízható: PIR (AM312) vagy mmWave radar (LD2410).

## Mozgás-trigger a kamerához

Arra kell, hogy a felismerés ne fusson feleslegesen.

| Módszer | Előny | Hátrány |
|---|---|---|
| **Képkülönbség** (96×96 szürke, 2–5 fps) | nincs plusz hardver, nincs ébredési késés | a kamera folyamatosan megy (melegedés, ~100–150 mA) |
| **Wi-Fi CSI az S3-on** | a kamera üresjáratban alhat (PWDN) | 0,5–1 s ébredés és AE-beállás; hangolni kell |
| **PIR** | hangolás nélkül megbízható | plusz alkatrész |

**Javaslat:** a képkülönbséggel indulunk, közben a CSI is fut és naplóz. A kamera eseményei megbízható referenciát adnak a CSI-küszöb hangolásához. Ha a CSI minden valódi belépést elkap, a kamera alhat, és a CSI ébreszti. Ha nem, marad a képkülönbség vagy a PIR. A téves CSI-riasztás csak egy felesleges felismerést indít, az elmaradt riasztás a valódi hiba.

## Wi-Fi-alapú mozgásérzékelés (CSI) röviden

- Az emberi test megváltoztatja a 2,4 GHz-es jel terjedési útjait. A CSI alvivőnként (~52–64) adja az amplitúdót és a fázist, ezért sokkal érzékenyebb, mint az egyetlen RSSI-érték.
- **Mozgást** jól érzékel, álló embert alig. Belépésnél ez elég.
- Lépések: baseline üres szobában → alvivő-amplitúdó variancia mozgó ablakban (~1 s) → küszöb hiszterézissel → lassú, adaptív baseline.
- Téves riasztás forrásai: ventilátor, függöny, mozgás a fal túloldalán, robotporszívó.
- Kiindulópontok: Espressif `esp-csi` / `esp-radar`, ESPectre (ESPHome).
- Az ESP32-C3 és az S3 is támogatja a CSI-t.

## Elvetett vagy háttérbe szorult irányok

- **Csak ajtónyitás-érzékelő (reed relé):** nem mondja meg, ki jött be, és hova tart. Legfeljebb megerősítő jelnek jó.
- **Telefon / MAC / IP alapú azonosítás:** a MAC-randomizáció miatt csak a hálózaton állandó privát MAC használható. A telefon alvás közben késve jelenik meg. A „lakótárs + vendég” esetet nem tudja kezelni. Kiegészítő jelnek (pl. élesítéshez) még hasznos lehet.
- **Személyazonosítás CSI-ből (járásminta):** kutatási szint, otthoni körülmények között törékeny.

## Ellenőrizendő kockázatok

- **Keretrendszer:** az arcfelismeréshez ESP-IDF + `esp-who` / ESP-DL kell. Úgy tudjuk, hogy az Arduino-ESP32 3.x-ből kikerült az arcfelismerés; az első lépésnél ellenőrizni kell. A kamerás csomópont külön projekt, az AquaLight maradhat Arduino.
- **Portolás:** az `esp-who` példák ESP32-S3-EYE-ra készültek LCD-vel. Át kell írni webes vagy soros kimenetre.
- **CSI az AquaLight firmware-ben:** a pioarduino előre fordított könyvtáraiban be van-e kapcsolva a CSI (`CONFIG_ESP_WIFI_CSI_ENABLED`). Ha nincs, `custom_sdkconfig` kell. Ellenőrizni kell azt is, hogy az egymagos C3-on a CSI-feldolgozás elfér-e a webszerver, a PWM és az OTA mellett.
- **Rádiócsatorna:** az ESP-NOW, a STA és a CSI ugyanazon a csatornán kell, hogy fusson, mint a router. Az AP+STA mód kölcsönhatását is vizsgálni kell.
- **Felismerés minősége:** szemből jövő arc kell, és egy belépésből csak 1–3 használható kép lesz. Kapucni, maszk vagy háttal belépés esetén az eredmény „bizonytalan”.

## Javasolt lépések

1. **`cam_node/`** külön mappa (ESP-IDF + `esp-who`): kamera indítása, képkülönbség-alapú mozgás, arcdetektálás és felismerés, regisztráció webes felületről (5 kép/fő), napló képtárolás nélkül.
2. **Mérés az előszobában:** felismerési távolság, idő, téves elfogadás és téves elutasítás, valódi belépéseken.
3. **CSI-naplózás** a kamera mellett, a küszöb hangolása a kamera eseményeihez.
4. **Szobaajtó-zóna** a két AquaLight között (CSI), vagy PIR.
5. **ESP-NOW állapotüzenetek** és az állapotgép az AquaLight firmware-ben, csak a látható fény lekapcsolásával.

## Nyitott kérdések

- Ha egy **lakótárs** megy be a szobába, legyen-e arra reakció?
- Menjen-e **kép** az értesítéssel? Egy pl. Telegramra küldött kép ott tárolódik, ami ütközik a „harmadik fél adata nem tárolódik” döntéssel. Alternatíva a szöveges értesítés.
- Hogyan élesedik a rendszer: kézzel, időzítéssel, vagy a tulajdonos telefonjának / felismert távozásának alapján?
- Mennyi ideig tartson az „idegen bent” állapot?
