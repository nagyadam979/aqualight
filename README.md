# AquaLight

ESP32-C3 SuperMini akváriumvilágítás két fehér LED-szalaggal és AHT20-szenzorral. A teljes webes felület és a világításvezérlés az ESP-n található, külső JavaScript nélkül. Az opcionális, aláírt firmware-frissítés külön Firebase-csatornát használ; a világítás internet és felhő nélkül is működik.

| Jel | GPIO |
| --- | --- |
| Meleg fehér, 2700 K | 0 |
| Hideg fehér, 6500 K | 4 |
| AHT20 SDA | 1 |
| AHT20 SCL | 3 |

A PWM alapértéke 1 kHz, 10 bit, aktív magas vezérlés. A polaritás a `src/main.cpp` elején csatornánként változtatható.

## Egycsatornás változat

Az egycsatornás lap (ugyanaz az ESP32-C3 SuperMini) külön PlatformIO környezetet használ: `aqualight1ch`.

| Jel | GPIO |
| --- | --- |
| PWM (egyetlen LED-csatorna) | 4 |
| AHT20 SDA | 1 |
| AHT20 SCL | 0 |

```sh
pio run -e aqualight1ch
pio run -e aqualight1ch -t upload
UI_CHANNELS=1 node test_ui.cjs
```

Az egycsatornás lap vezérlőfelülete egyetlen görbét és egyetlen kézi csúszkát mutat; az API ugyanaz marad (a `cool` mező ezen a lapon mindig 0, a `coolPoints` a `warmPoints` másolata). Saját webcíme `http://led-control-1ch.local/`, a kétcsatornásé `http://led-control.local/`. A Wi-Fi-, mDNS- és ArduinoOTA-eszköznév is ezt a szétválasztást követi.

Az OTA külön csatornát használ: a manifeszt célja `led-control-esp32c3-1ch`, a csatornafájl `channels/stable-1ch.json`, és a kiadási azonosító `1ch-` előtagú. Így a két lap firmware-e nem cserélhető fel. Kiadás: `python3 release_firmware.py --board 1ch --firmware .pio/build/aqualight1ch/firmware.bin --release-id 1ch-1.1.0 --version 1.1.0`.

## Közös vezérlőoldal

Bármelyik lap webcímét megnyitva ugyanaz a közös nyitóoldal jelenik meg, rajta **egyszerre mindkét lap teljes vezérlésével**. Nagy képernyőn egymás mellett, mobilon egymás alatt látható a **Kétcsatornás lap** és az **Egycsatornás lap** panelje. Mindkét panel saját programot, csúszkákat, szenzoradatokat és beállításokat kezel; a parancsok közvetlenül az adott laphoz jutnak. A két panel egymástól függetlenül őrzi a még nem mentett görbeszerkesztést; a teljes böngészőoldal újratöltése ezt nem őrzi meg. A panelek tartalma szükség esetén külön görgethető.

A közös eléréshez **mindkét lapra ezt a firmware-változatot kell feltölteni**, mindkettőt ugyanarra az otthoni Wi-Fi-hálózatra kell csatlakoztatni, és a böngészőnek fel kell tudnia oldani a `.local` neveket. Egy egycsatornás és egy kétcsatornás lapot támogat; több azonos típusú laphoz külön címzés szükséges. A nyitóoldal IP-címmel vagy a saját AP `192.168.4.1` címén is működik a helyi lappal, de a másik lap csak akkor nyitható meg, ha a böngészőt futtató eszköz arról a hálózatról eléri. Kikapcsolt vagy nem elérhető másik lap nem akadályozza a helyi vezérlést. A panelek saját **külön megnyitása** hivatkozása az adott vezérlést önálló oldalon nyitja meg; közvetlenül a `/control` útvonalon is elérhető.

## Fordítás és feltöltés

```sh
pio run -e aqualight
pio run -e aqualight -t upload
pio device monitor
python3 test_controller.py
python3 test_maintenance.py
python3 test_ota.py
node test_ui.cjs
```

A PlatformIO platform és az AHTxx-könyvtár verziója rögzített. Az USB-s napló 115200 baudon érhető el. A natív teszt C++17-fordítót és annak AddressSanitizer/UBSan támogatását használja; nem igényel külön tesztcsomagot.

## Első használat

1. A soros naplóban megjelenik az `AquaLight-…` Wi-Fi neve és az első induláskor generált, eszközönként különböző jelszó. A jelszó újraindításkor megmarad.
2. Csatlakozz ehhez a hálózathoz, és nyisd meg a `http://192.168.4.1/` címet. Az ESP akkor is kiszolgálja az oldalt, ha nincs internet.
3. A hálózati beállításoknál opcionálisan add meg az otthoni Wi-Fi adatait. Csatlakozás után a naplóban jelzett IP-címen, illetve mDNS-t támogató eszközön a kétcsatornás lap a `http://led-control.local/`, az egycsatornás a `http://led-control-1ch.local/` címen is elérhető. A közös nyitóoldalon a helyi lap paneljében állítsd be a hálózatot. A saját Wi-Fi tartalék hozzáférésként megmarad.
4. Állítsd be az órát a telefon/böngésző idejéről, vagy internetkapcsolat mellett várd meg az NTP-szinkront. Az óra magyar téli–nyári időszámítást használ.
5. A napi grafikonon válaszd ki a meleg vagy hideg csatornát. A pontok húzásával módosíthatod az időt és a fényerőt, dupla koppintással új pontot adhatsz hozzá. A változások csak a Mentés után kerülnek a lámpára; ezután válts automata módra. A kézi vezérlés külön lenyitható részben érhető el; külön gombbal kapcsolható be, kikapcsolása automata módra tér vissza. A csúszkák csak bekapcsolt kézi módban állíthatók.

Új eszközön a lámpa kikapcsolt módban indul. A szerkeszthető mintaprogram 08:00–09:00 között 0-ról 50%-ra emel mindkét csatornán, 17:00-ig tartja, majd 18:00-ig lecsökkenti 0-ra. Mentése önmagában nem kapcsol automata módra.

Mindkét csatorna saját 2–16 egyedi időpontból álló görbét tárol; a meleg pontok szerkesztése nem változtatja meg a hideg csatorna időpontjait vagy fényerejét. A csatornák a saját pontjaik között folyamatosan, lineárisan változnak, az utolsó ponttól az elsőig éjfélen át is. A helyi óra tavaszi előreállásakor az átugrott óra kimarad; ősszel az ismétlődő helyi óra megismétli a görbe adott szakaszát.

A saját AP elérhetőségéért az állomáscsatlakozásnak 15 másodperces határideje van. Sikertelen próbálkozás után 30, 60, majd legfeljebb 120 másodperc szünet következik, az AP kikapcsolása nélkül; sikeres csatlakozás visszaállítja a rövid várakozást. A natív, késleltetés nélküli automatikus újracsatlakozás ki van kapcsolva. A hálózati napló jelszavak nélkül jelzi a hibakódot, az utolsó jelerősséget, az AP csatornáját és a következő próbálkozást. Az ESP32-C3 SuperMini rádiójának alapkalibrációja 8,5 dBm; más laphoz a `LED_WIFI_TX_POWER` fordítási makróval változtatható, például a natív `WIFI_POWER_19_5dBm` értékre. A tényleges beállított teljesítmény a naplóban visszaolvasható.

A mód, a program és a kézi fényerő megmarad újraindításkor. A kézi csúszkák beállítása az utolsó módosítás után 2 másodperccel kerül flashbe; a program és a módváltás azonnal mentődik. Áramszünet után automata módban a kimenetek érvényes új időszinkronig kikapcsolva maradnak. A rendszer nem állít vissza egy régi, elmentett dátumot. Kézi módban óra nélkül is világít a beállított értékkel.

Az AHT20 hibája a lámpavezérlést nem állítja le. A hibás mérés helyett a felület érvénytelen adatot jelez, és az ESP újrapróbálja a szenzor inicializálását. PWM-hiba esetén mindkét kimenet kikapcsolt állapotba kerül.

## Helyi API

A `GET /api/state` az aktuális állapotot adja vissza JSON-ként, jelszavak nélkül. A `schedule` mező két külön listát tartalmaz: `{"warm":[{"minute":480,"level":0},…],"cool":[{"minute":510,"level":0},…]}`. A módosító kérések `POST`, `application/x-www-form-urlencoded` tartalommal és `X-Aqua-Request: 1` fejléccel működnek. A fejléc a más weboldalakról indított böngészős kéréseket korlátozza; a hálózaton lévő kliensek számára nem helyettesít felhasználói hitelesítést. A vezérlőt saját, megbízható helyi hálózaton használd, porttovábbítás nélkül.

| Végpont | Mezők |
| --- | --- |
| `/api/mode` | `mode=off`, `manual` vagy `auto` |
| `/api/manual` | `warm=0..100`, `cool=0..100`; csak az előzőleg `/api/mode` kéréssel bekapcsolt kézi módban állít értéket, egyébként HTTP 409, változtatás nélkül |
| `/api/program` | `warmPoints=perc,fényerő;perc,fényerő;…` és `coolPoints=perc,fényerő;perc,fényerő;…`; csatornánként 2–16 növekvő, egyedi percérték 0–1439 között |
| `/api/time` | `epoch=` UTC Unix-másodperc, 2024–2099 között |
| `/api/update/check` | üres űrlap; internetes Wi-Fi és érvényes óra szükséges, HTTP 202 csak a keresés sorba állítását jelzi |
| `/api/wifi` | `ssid=`, `password=`; a jelszó üres nyílt hálózatnál, egyébként 8–63 bájt |

A sikeres válasz `{"ok":true}`. Hiba esetén a válasz HTTP-hibakódot és `{"ok":false,"error":"…"}` objektumot tartalmaz. A program mentése atomikus: ha bármelyik csatorna görbéje érvénytelen, egyik aktív görbe sem változik meg. A Wi-Fi-mentés sikere a csatlakozási kísérlet indítását jelzi; a tényleges kapcsolat az állapotból derül ki. A kikapcsolás tárolóhiba esetén is végrehajtódik, bár a mentés hibát jelezhet.

A hostteszt a két független görbét, az atomikus kettős validálást, a parsereket, módokat, PWM-számítást, időzítést és az időzóna váltásait ellenőrzi. A valódi PWM-jelet, fényerőt, szenzort, HTTP-kiszolgálást, Wi-Fi-t és flashbe mentést hardveren kell ellenőrizni.

A `node test_ui.cjs` a tényleges beágyazott JavaScriptet futtatja egyszerű DOM- és hálózati modellel. A független görbéket, húzást, dupla koppintást, pontkorlátokat, fókuszt és a mentés/módváltás versenyhelyzeteit ellenőrzi; a valódi böngészős érintéskezelést és megjelenést nem bizonyítja.


## Firmware-frissítés

Az alkalmazás verziója a `LED_CONTROL_VERSION` fordítási makró; az alapkiadás **1.1.0**. Az `update` és `maintenance` állapotmezők jelszó nélkül jelzik a frissítés állapotát. A `min_spiffs.csv` partíciós tábla két `0x1e0000` méretű alkalmazáshelyet használ; az NVS címe/mérete (`0x9000`, `0x5000`) és az `otadata` (`0xe000`, `0x2000`) megmarad. A partíciós tábla egyszeri telepítése USB-n történik, az NVS törlése nélkül. Ezután az OTA csak az inaktív alkalmazáshelyet írja.

A webes frissítés gomb külön, nyilvános Firebase-kiadási csatornán keres új firmware-t. A kiadás aláírt manifesztből és alkalmazásképből áll; a nyilvános firmware nem tartalmaz Wi-Fi- vagy eszközjelszót. A kiadási cél a BeeScale által is használt `terrarium1` Firebase-projekt külön `led-control-terrarium1` Hosting-oldala; a csatorna címe `https://led-control-terrarium1.web.app/channels/stable.json`. A meglévő BeeScale-oldal és a CafePlant projekt változatlan. A vezérlő indulás után 60 másodperccel, majd sikeres ellenőrzések után 30 percenként keres kiadást; a webes gombbal korábban is indítható keresés. A hálózati letöltés külön munkaszálon fut. A beállításmentés befejezése előtt nincs szándékos újraindítás; az új felhős kép indulási állapotát a vezérlő ellenőrzi.

A privát aláírókulcs a repositoryn kívül, a `~/.config/led-control/ota-signing-key.pem` fájlban marad, `0600` jogosultsággal. A firmware-be és Gitbe csak a `src/ota_public_key.h` nyilvános ellenőrzőkulcsa kerül. A kiadóeszköz OpenSSL 3.2 vagy újabb verziót használ; alapértelmezése dry run, fájlírás és publikálás nélkül.

```sh
python3 release_firmware.py --firmware .pio/build/aqualight/firmware.bin \
  --release-id 1.1.0 --version 1.1.0
python3 release_firmware.py --firmware .pio/build/aqualight/firmware.bin \
  --release-id 1.1.0 --version 1.1.0 --prepare \
  --output "$HOME/.local/share/led-control/hosting"
firebase deploy --project terrarium1 \
  --config "$HOME/.local/share/led-control/hosting/firebase.json" \
  --only hosting --non-interactive
```

A `--prepare` csak a külön, repositoryn kívüli Hosting-kimenetet készíti elő. Kizárólag az utolsó, külön kiadott Firebase-parancs publikál; annak konfigurációja a dedikált `led-control-terrarium1` site-ot nevezi meg. Korábbi kiadások megmaradnak, azonos kiadásazonosító eltérő képpel nem írható felül.

Helyi, hitelesített **ArduinoOTA** a kétcsatornás lapon `led-control.local:3232`, az egycsatornáson `led-control-1ch.local:3232` címen is elérhető. Jelszava az eszköz meglévő, egyedi AP-jelszava, amely a soros indulási naplóban látható; nem kerül API-válaszba vagy Telnet-naplóba. Csak alkalmazásképet tölts fel (`firmware.bin`), ne teljes flashképet vagy fájlrendszerképet. Az Arduino **3.3.8** csomaghoz tartozó `tools/espota.py` feltöltőt használd: ez támogatja a PBKDF2-HMAC-SHA256 hitelesítést, a régebbi feltöltők nem feltétlenül kompatibilisek.

Példa a már elkészített alkalmazáskép feltöltésére; a jelszót interaktívan olvasd be, ne mentsd a projektbe:

```sh
read -s LED_OTA_PASSWORD
python3 ~/.platformio/packages/framework-arduinoespressif32/tools/espota.py \
  -i led-control.local -p 3232 -a "$LED_OTA_PASSWORD" \
  -f .pio/build/aqualight/firmware.bin
unset LED_OTA_PASSWORD
```

A helyi és felhős OTA közös zárral védi a flashírást. Helyi feltöltés előtt a függő kézi beállítások mentődnek; mentési hiba esetén a feltöltés megszakad. Sikeres helyi feltöltés után az újraindítás szintén a biztonságos mentési állapotra vár.

## Telnet diagnosztika

A `telnet led-control.local 23` (egycsatornás lapon `telnet led-control-1ch.local 23`) egyetlen klienssel használható, ugyanazzal az egyedi AP-jelszóval. A belépés 10 másodperc után lejár, legfeljebb három hibás próbálkozás engedélyezett; ezután 30 másodperces szünet következik. Az inaktív munkamenet 5 perc után bezárul. A Telnet titkosítatlan helyi protokoll, ezért megbízható LAN-on/AP-n használd; nem általános parancsértelmező és nem tükrözi a soros naplót.

| Parancs | Művelet |
| --- | --- |
| `help` | Engedélyezett parancsok |
| `status` | Olvasható világítás-, szenzor-, tároló- és frissítési pillanatkép, jelszavak nélkül |
| `ota-check` | Aláírt felhős frissítés keresésének sorba állítása |
| `reboot` | Újraindítás a függő beállítások sikeres mentése után |

A parser legfeljebb 96 karakteres sorokat, CRLF/CR-NUL lezárást és alap Telnet IAC-egyeztetést kezel. A bemenet, kimeneti sor és a ciklusonkénti munka korlátozott; nem olvasó kliens nem fogja meg a vezérlést. A `python3 test_maintenance.py` az éles parser/beléptetés kódját ASan/UBSan mellett ellenőrzi; TCP-t, OTA-átvitelt, flashváltást és újraindítást hardveren kell ellenőrizni.

## Licenc

MIT – lásd a [LICENSE](LICENSE) fájlt.
