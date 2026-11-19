# AquaLight

ESP32-C3 SuperMini terráriumvilágítás teljes spektrumú (lila, kék + piros) és hideg fehér LED-szalaggal és AHT20-szenzorral. A teljes webes felület és a világításvezérlés az ESP-n található, külső JavaScript nélkül. Az opcionális, aláírt firmware-frissítés külön Firebase-csatornát használ; a világítás internet és felhő nélkül is működik.

## A terrárium fővezérlője

A kétcsatornás ESP32-C3 önállóan vezérli a terrárium világítását és méri a klímáját. A helyi weboldal egyben átjáró az egycsatornás LED-szalag vezérlőjéhez.

| Feladat | Működés |
| --- | --- |
| Világítás | A GPIO0 a teljes spektrumú, a GPIO4 a hideg fehér csatornát vezérli. Mindkettőnek saját kézi fényereje és 2–16 pontos napi görbéje van. A pontok között a fényerő folyamatosan változik. A mód, a görbék és a kézi értékek NVS-ben maradnak meg. |
| Idő | A napi program magyar helyi idő szerint fut. A master NTP-ből, böngészőből vagy saját érvényes idő hiányában a még járó társvezérlőtől kaphat időt. Teljes áramtalanítás után a kézi mód azonnal működik, az automata az óraszinkronra vár. |
| Klíma | Az AHT20 külön feladatban mér. A főciklus a lezárt tízperces összesítéseket flashbe menti, és a helyi felületen letölthetővé teszi az előzményeket. [A mérési napló részletei](#terrárium-mérési-naplója) |
| Hálózat és kezelés | A master az otthoni Wi-Fi-n szolgálja ki mindkét panelt. A párosított LED-szalaggal titkosított ESP-NOW kapcsolaton cserél állapotot és parancsokat. Mindkét board önállóan futtatja a mentett programját. A tartalék hotspotot a LED-szalag biztosítja. |
| Karbantartás | A master USB-n, helyi ArduinoOTA-val vagy aláírt felhős kiadásból frissíthető. Az USB-s állapotlekérdezés és a helyi Telnet diagnosztikát ad. A webfelületről elindítható a társvezérlő karbantartási hotspotja. |

A Wi-Fi-s master az aktív változat. Az alábbi BLE-próba külön, félretett kísérlet.

## Androidos BLE-próba – 1.4.0-ble

Az új pilot a terrárium kétcsatornás boardját közvetlenül Android Chrome-ból vezérli, titkosított BLE-kapcsolattal. A telefonos felület bejelentkezés nélkül megnyitható HTTPS-oldal; címe a helyi, Gitből kizárt `.ble-deployment.json` fájlban található. A felület elkészült és közzé van téve; a hálózati firmware-átvitel megszakadt, a master még 1.3.2-t jelez. A BLE-verzió USB-s feltöltése és a tényleges telefonos próba hátravan. A fordítás önmagában nem bizonyítja az eszközre telepítést.

A pilot csak BLE-t indít: otthoni Wi-Fi, saját hotspot, ESP-NOW, HTTP-kiszolgáló és felhős frissítési worker nem fut. A helyi PWM- és szenzorfeladatok, az NVS-programok és a program szerkesztője megmaradnak. A led-szallag ebben az első próbában még nem BLE-célpont; a két board közös rádiós működését külön kell majd ellenőrizni.

Chrome-ban a **Csatlakozás** gomb indítja az eszközválasztást és az Android rendszeres PIN-párosítását. A hatjegyű PIN külön, helyi `.ota-credentials-ble.json` fájlban van, `0600` jogosultsággal; nem kerül a weboldalba, URL-be vagy a gyorsítótárba. A GATT-hozzáférés titkosított, hitelesített, Secure Connections párosítást igényel. Hitelesítés nélkül 45 másodperc után bont; ismételt parancsazonosító nem hajt végre még egyszer módosítást.

Csatlakozáskor a felület lekéri az állapotot és beállítja a telefon idejét. Teljes áramtalanítás után automata módban ez szükséges a fény indulásához; a már járó óra és világításprogram a telefon leválasztása után is tovább fut. Natív OTA-telepítéskor a pilot csak sikeres telefonos időbeállítás után fogadja el az új képet; ennek hiányában 180 másodperc után a korábbi érvényes firmware-re áll vissza. A BLE-pilotban BLE-n továbbított firmware-frissítés nincs; további frissítéshez USB használható.

A felület első sikeres HTTPS-betöltés után a statikus eszközeit offline gyorsítótárazza. Szenzorállapot, PIN és parancs nem kerül az offline gyorsítótárba. Kapcsolatvesztéskor a vezérlés letiltódik, a szerkesztési vázlat megmarad; módosításokat nem küld automatikusan újra.

```sh
python3 tools/generate_ble_config.py --init  # csak egyszer; meglévő fájlt nem ír felül
pio run -e aqualight_ble
python3 tools/export_ble_ui.py
python3 test_ble_link.py
python3 test_ble_config.py
python3 test_ble_app.py
node test_ble_ui.cjs
```

NimBLE-Arduino: rögzített `2.5.2`. Kiadási könyvtár: `ble-web/dist`; a külön Sites-forrástár és projektazonosító a `ble-web/` alatt marad, privát firmware-konfiguráció nélkül. A build- és forráshasheket a `diagnostics/1.4.0-ble-artifacts.json` rögzíti. A böngészős vizuális ellenőrzést a számítógépes böngészővezérlés elérhetetlensége korlátozta; a valódi JavaScriptet futtató BLE- és UI-tesztek sikeresek.

## Master/slave architektúra – 1.3

A **terrárium** kétcsatornás boardja a **master/gateway**, a **led-szallag** egycsatornás boardja a **slave**. A master csatlakozik az otthoni Wi-Fihez, kiszolgálja a közös kezelőfelületet és időt szinkronizál. A slave normál üzemben ESP-NOW-on kommunikál, otthoni Wi-Fi-csatlakozás és folyamatos hotspot nélkül.

Mindkét board saját NVS-ben tartja és helyben futtatja a saját világításprogramját. A PWM-vezérlés külön, magasabb prioritású feladat; az AHT20 külön szenzorfeladatban fut. A hálózati főciklus egyikre sem vár. A rádiós fogadás csak véges sorba másol, a HTTP-kiszolgálás négy klienssel, nem blokkoló socketműveletekkel, ciklusonkénti bájtkerettel és abszolút/idle határidőkkel működik. A böngésző számára a HTML előre tömörített gzip-változata is elérhető. Routerkimaradás miatt nincs automatikus világításmegszakító újraindítás.

Normál elérés: `http://led-control.local/` vagy a master LAN IP-címe. A két panel azonos eredetű kéréseket használ; a led-szallag közvetlen panelje: `/control?target=peer&channels=1`. A slave korábbi LAN IP-címe és `.local` neve normál üzemben nem külön hálózati célpont. A master a slave **utolsó ismert állapotából** válaszol, nem vár rádiós válaszra a HTTP-kérésben. A minta korát jelzi; kapcsolatvesztéskor a távoli módosítás letiltódik, a helyi vezérlés tovább fut.

A slave saját `AquaLight-…` hotspotja indulás vagy hiba után 15 másodperccel válik szükségessé, ha a master vagy annak otthoni Wi-Fi-kapcsolata nem elérhető. Harminc másodperces stabil helyreállás után, AP-kliens nélkül leáll. Kézzel a slave **BOOT gombjának 3 másodperces nyomásával, RESET nélkül**, illetve a felület **Karbantartási hotspot indítása** gombjával tíz percre indítható. Egy csatlakozott AP-klienst nem dob le lejárat vagy helyreállás miatt. A hotspot címe `http://192.168.4.1/`; jelszava a korábbi saját AP-jelszó marad.

A master rádiócsatornáját az otthoni Wi-Fi határozza meg. A slave az utolsó közös csatornáról indul, kapcsolatvesztéskor véges időlépésekben keres. Aktív AP-kliens mellett nem váltogatja a csatornát. A helyi világításprogram és a már beállított óra a kapcsolat nélkül is tovább működik. Teljes áramtalanítás után az automata indulásához ismét érvényes idő szükséges: a master NTP-ből vagy a böngészőtől kapja, a slave ESP-NOW-on átveszi. A slave helyi órája master nélkül is beállítható.

Az 1.3.1-től a master legfeljebb 15 másodperces Wi-Fi-csatlakozási próbája külön rádiós időablak: közben az új ESP-NOW-adás és driver-helyreállítás szünetel. A fogadás, protokollhatáridők és helyi vezérlés futnak. Feloldáskor a függő MAC-visszaigazolás új türelmi időt kap.

Az 1.3.2-től az érvénytelen órájú master a még járó slave hitelesített, érvényes idejét is átveheti. Ez csak saját érvényes idő hiányában történik; egy már érvényes master-/NTP-óra továbbra is elsődleges. Ha mindkét board teljesen áramtalanodott, változatlanul új külső időszinkron szükséges.

USB-s diagnosztika 1.3.2-től: a pontos `status` sor CR/LF lezárással egy kompakt JSON-állapotot kér le. Csak olvasás: verzió, uptime, IP, STA/peer kapcsolat, óra, vezérlési és szenzorszámlálók. A parser fix méretű, ciklusonként legfeljebb 32 bemeneti bájtot dolgoz fel; a kérés két másodperc alatt lejár. A válasz legfeljebb 351 bájt, a fizikai USB írási várakozása rövid. A sima portolvasás DTR/RTS-vezérlés nélkül használható; egyes soros monitorok portnyitáskor resetet váltanak ki ezen a boardon.

A távoli parancs saját azonosítót, boot-sessiont, véges újrapróbálást és alkalmazásszintű ACK-ot kap. A MAC-szintű rádiós nyugta nem jelent végrehajtást. Program- és módmentésnél a célboard sikeres mentése után érkezik ACK; kézi fényerőnél a korábbi RAM-alkalmazás és kétmásodperces mentési debounce marad, ezt a `savePending` mutatja. Elveszett ACK esetén a végrehajtás nem igazolt, ezért nincs automatikus visszaállítás vagy végtelen ismétlés. A célkonfiguráció revíziójának megérkezése is szükséges a felületi sikerhez.

A master helyi API-ja megmarad. A peer API a `/api/peer/` előtagot használja; a távoli POST HTTP 202 és `commandId` választ ad, eredménye a `GET /api/peer/command?id=…` végponton kérdezhető. A `/api/peer/state` a gyorsítótárazott állapothoz `transport` frissességi adatot ad. A Wi-Fi-űrlap a slave hotspotjáról is a master otthoni kapcsolatát állítja.

Frissítés: a master a helyi OTA-t és az aláírt felhős csatornát használja. A slave a saját karbantartási hotspotján helyi ArduinoOTA-val, vagy USB-n frissíthető; firmware-kép ESP-NOW-továbbítás ebben a verzióban nincs. A master kezelőfelületéről a slave hotspotja bekapcsolható.

### Párosítás és fordítás

A két ismert MAC-címhez generált, titkosított unicast-kapcsolat privát PMK/LMK kulcsai a Gitből kizárt, `0600` jogosultságú `.ota-credentials-mesh.json` fájlban vannak. A build ebből kizárólag `.pio/` alatti privát fejlécet készít; a kulcsok nem kerülnek naplóba vagy API-ba. Új, saját párhoz egyszer futtatandó:

```sh
python3 tools/generate_mesh_config.py --init \
  --master-mac 02:00:00:00:00:01 --slave-mac 02:00:00:00:00:02 --channel 1
```

Meglévő privát fájlt a parancs nem ír felül. Az így készült firmware-ek az adott pár privát kulcsait tartalmazzák; a korábbi nyilvános Firebase-kiadási példák nem alkalmazhatók változtatás nélkül ezekre a képekre.

A `tools/generate_web_assets.py` automatikusan és determinisztikusan készíti a tömörített oldalakat. A `tools/upload_local_ota.py` a telepített Arduino-feltöltő hitelesítését használja, folyamatos TCP-küldéssel és párhuzamos válaszolvasással; alapértelmezése csak preflight. A tényleges feltöltéshez külön `--upload` kapcsoló szükséges. Jelszót az ignorált helyi credential-fájlból olvas, nem parancssori argumentumból.

| Jel | GPIO |
| --- | --- |
| Teljes spektrumú, lila (kék + piros) | 0 |
| Hideg fehér, 6500 K | 4 |
| AHT20 SDA | 1 |
| AHT20 SCL | 3 |

Mindkét változat aktuális PWM-je 1 kHz, 13 bit, aktív magas vezérléssel. A polaritás a `src/main.cpp` elején csatornánként változtatható.

Az **1.2.5-ös terrárium-firmware** a felhasználó kérésére visszaállítja az 1.2.2-ben használt **1000 Hz / 13 bit** PWM-et és a korábban 5%-nál láthatóan bekapcsoló fő-LED-görbét. Az 1–5%-os állások teljes spektrumon `1,4,14,30,52`, hideg fehéren `1,6,21,47,82` PWM-egységet adnak; 0% kikapcsolás. Az 1–4%-os elektromos PWM-tartomány megmarad a tervezett apró kiegészítő LED-ekhez; külön új GPIO vagy automatikus kis-LED-vezérlés nincs hozzáadva. A nevek, felület és Wi-Fi-helyreállítás változatlan, a led-szallagra nem történt új feltöltés.

A terráriumra korábban feltöltött **1.2.4-es kalibrációs próba**: a terrárium 200 Hz mellett 14 bites felbontást kap, kb. 0,305 µs-os időlépéssel. Az első szint 21/33 egység (kb. 6,409/10,071 µs), az 1.2.3-as 11/17 egység (6,714/10,376 µs) helyett. Ez 4,5%/2,9% impulzusrövidítés; **nem igazolt ötödnyi fény**, a LED-áram és a modul kapcsolási átmenetének optikai hatását a felhasználó visszajelzése alapján kell kalibrálni. Az 5%-os állás 104/164 egysége ugyanazt a kitöltési arányt jelenti, mint korábban az 52/82. A led-szallag 13 bites skálája változatlan. A jelenlegi kétcsatornás board aktív magas kimeneteit támogatja; a 14 bites indulást aktív alacsony polaritással fordítási ellenőrzés tiltja.

A maximális felbontásnál a `16384` kizárólag matematikai végérték: LEDC-duty-regiszterbe nem írható. A 0/100%-os fizikai kimenetet `ledc_stop(..., idle)` állítja elő, köztes értékre a `ledc_set_duty` és `ledc_update_duty` újra engedélyezi a PWM-et. Ezt az [ESP-IDF v5.5.4 forrásában](https://raw.githubusercontent.com/espressif/esp-idf/v5.5.4/components/esp_driver_ledc/src/ledc.c) ellenőriztük. A `test_pwm_output.py` az éles kiírófüggvény végértékeit és visszakapcsolását teszteli.

Az 1.2.3-as terrárium-próba a korábban 1 kHz-en megfigyelt bekapcsolási impulzushosszt tartja meg, 200 Hz-es ismétléssel. Az új 1% teljes spektrumon 11, hideg fehéren 17 PWM-egység: kb. 6,71 µs, illetve 10,38 µs hosszú impulzus. A korábbi megfigyelt küszöbök 6,35 és 10,01 µs voltak (a beállításokból számolva, nem oszcilloszkópos mérésből). Az 5%-os állás továbbra is 52/82 PWM-egység; a kitöltés azonos, de a fényerő a kapcsolási átmenetek miatt megváltozhat. Az 1–5%-os értékek 11,14,21,34,52 és 17,21,33,54,82. A cél használható minimális impulzusok ritkább ismétlése; a halvány fényt és az esetleges vibrálást a felhasználónak a lámpán kell ellenőriznie. A led-szallag leképezése és frekvenciája változatlan.

A felhasználó szerint a fehér ágban három power LED van sorban, teljesítményellenállásos áramkorlátozással, LR7843 kapcsolással. A pontos tápfeszültség, ellenállásérték és modulváltozat nincs rögzítve. A [megadott LED termékoldala](https://www.microcontroller.hu/termek/hideg-feher-power-led-3w/) darabonként 3,2–3,4 V-ot és 700 mA-t ad meg. PWM esetén a fényt az impulzusok alatti áram és azok időaránya határozza meg, a multiméterrel mérhető átlagfeszültség nem egyenértékű ugyanakkora egyenfeszültséggel. A gyakori optocsatolós LR7843-modulnál az optocsatoló és a gate-meghajtás is korlátozhat: például a [Sharp PC817 adatlap](https://media.digikey.com/pdf/Data%20Sheets/Sharp%20PDFs/PC817X%20Series.pdf) tipikus 4/3 µs fel-/lefutást ad meg a saját vizsgálati feltételein. Ez a konkrét modulra vonatkozóan egyelőre lehetséges magyarázat.

Az 1.2.2-es terrárium-finomítás: a kétcsatornás board jelenlegi 1%-os értéke az új 5%-hoz kerül (teljes spektrumú: 52, hideg fehér: 82 PWM-egység). Az új 1% mindkét csatornán 1 PWM-egység, a 13 bites vezérlés legkisebb nem nulla értéke. Az 1–5%-os szintek PWM-értékei rendre `1, 4, 14, 30, 52`, illetve `1, 6, 21, 47, 82`; köztük az automata program töredékes szintjei is folytonosan interpolálódnak, egész PWM-egységre kerekítve. 5% felett négyzetes görbe vezet a 100%-os teljes kitöltésig. A 0% továbbra is teljes kikapcsolás; a led-szallag leképezése változatlan.

Az 1.2.1-es fényerő-finomítás csatornánként mért minimumról indul: a terrárium teljes spektrumú kimenetén 52, hidegen 82, az egycsatornás kimeneten 66 PWM-egység. Ezek a korábbi, 1.2.0-s gamma-2 skála 8%, 10%, illetve 9% állásainak felelnek meg. Az új skálán **0% kikapcsolás, 1% az adott minimum**, 100% teljes kitöltés. 1% fölött a kitöltés `minimum + (8192 - minimum) × ((szint - 1) / 99)²`, így a 2%-os állás mindhárom kimeneten csak egyetlen PWM-egységgel erősebb az 1%-nál. Mind a 100 egész szabályozási lépés külön kimeneti értéket kap. Az automata program töredékes, 0–1% közötti szintjein a kimenet lineárisan halad a kikapcsolástól a küszöbig; ezt a LED nem feltétlenül jeleníti meg látható fényként.

A 13 bites felbontás a C3 14 bites maximuma alatt marad, így a teljesen bekapcsolt végérték is használható ([LEDC dokumentáció](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32c3/api-reference/peripherals/ledc.html)). A kezelőfelület, az API és a tárolt 0–100 közötti szintek változatlanok, de a PWM-leképezés módosul: a mentett napi program fényerejét az új változat későbbi telepítése után a lámpán újra ellenőrizni kell.

Az **1.2.6-os diagnosztikai változat** a fényvezérlés és a szenzor újrapróbálási ütemének módosítása nélkül közli az első és utolsó szenzorhibát, a mérési és inicializálási hibaszámlálókat, a hibakori SDA/SCL-szinteket, valamint az utolsó és maximális műveleti időket. Az adatokat a **Frissítés és diagnosztika → Működési diagnosztika** rész, a `/api/state` új `diagnostics` objektuma és a Telnet `status` is mutatja. A számlálók az utolsó újraindítás óta érvényesek; a sikeres helyreállás nem törli a hibaelőzményt. A főciklus és a HTTP-kezelés 500 ms feletti befejezett műveleteit külön számlálja; ezek mért idők, nem végrehajtási határidők. Memóriastatisztikát és újraindítási okot is közöl.

A szenzor mérési hibakódjai: `1` foglalt, `2` I²C ACK-hiba, `3` hiányos adat, `4` CRC-hiba. A helyi `253` inicializálási hibát, a `254` érvénytelen numerikus vagy tartományon kívüli mintát jelöl. Az AHTxx `begin/softReset` nem frissíti a mérési hibakódot, ezért az inicializálás eredményét külön kezeljük. Sikertelen inicializálás után egy 50 ms-os I²C-időkorláttal végzett címpróba külön közli a Wire eredményét (`0` ACK, `2` NACK, `5` időtúllépés); ez nem jelent busz-visszaállítást. Régebbi firmware-en a felület a hiányzó diagnosztikát kezeli.

2026. november 18-án a terráriumra USB-n felkerült az **1.2.6-os diagnosztikai változat**. A kiolvasott és a buildben lévő partíciós tábla bájtról bájtra azonos volt; az NVS-t a feltöltés nem írta. Az élő API az új verziót szolgálja ki, a program, mód és kézi értékek megmaradtak, PWM/tároló/óra rendben. A diagnosztika a weboldal lekérésekor **24,48 másodperces HTTP-blokkolást** mért; a szenzorhiba a rövid, eredeti táp és USB mellett végzett megfigyelésben még nem ismétlődött meg. A pontos mérési adatok a `diagnostics/2026-11-18-diagnostic-live.jsonl` és a telepítési összefoglaló fájlban vannak. A led-szallagra nem történt új feltöltés.

## Egycsatornás változat

Az egycsatornás board (ugyanaz az ESP32-C3 SuperMini) külön PlatformIO környezetet használ: `aqualight1ch`.

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

Az egycsatornás board vezérlőfelülete egyetlen görbét és egyetlen kézi csúszkát mutat; az API ugyanaz marad (a `cool` mező ezen a boardon mindig 0, a `coolPoints` a `warmPoints` másolata). Saját webcíme `http://led-control-1ch.local/`, a kétcsatornásé `http://led-control.local/`. A Wi-Fi-, mDNS- és ArduinoOTA-eszköznév is ezt a szétválasztást követi.

Az OTA külön csatornát használ: a manifeszt célja `led-control-esp32c3-1ch`, a csatornafájl `channels/stable-1ch.json`, és a kiadási azonosító `1ch-` előtagú. Így a két board firmware-e nem cserélhető fel. Kiadás: `python3 release_firmware.py --board 1ch --firmware .pio/build/aqualight1ch/firmware.bin --release-id 1ch-1.1.0 --version 1.1.0`.

## Korábbi közös vezérlőoldal – 1.2.x

A kétcsatornás eszköz megjelenített neve **terrárium**, az egycsatornásé **led-szallag**. A hálózati címek és az API `warm`/`cool` mezői a korábbi programokkal kompatibilisek maradnak; a `warm` mező a terrárium lila, teljes spektrumú kimenetét jelenti.

Bármelyik board webcímét megnyitva ugyanaz a közös nyitóoldal jelenik meg. A **terrárium** és **led-szallag** választófüllel ugyanazon az oldalon érheted el mindkét teljes vezérlést; egyszerre a kiválasztott board panelje látszik. Első megnyitáskor az oldalt kiszolgáló helyi board az aktív. A fülek között kattintással, érintéssel, illetve bal/jobb nyíllal és Home/End billentyűvel válthatsz. Mindkét panel saját programot, csúszkákat, szenzoradatokat és beállításokat kezel; a parancsok közvetlenül az adott boardhoz jutnak. A már megnyitott panel váltáskor nem töltődik újra, ezért a még nem mentett görbeszerkesztés megmarad; a teljes böngészőoldal újratöltése ezt nem őrzi meg.

A közös eléréshez **mindkét boardra ezt a firmware-változatot kell feltölteni**, mindkettőt ugyanarra az otthoni Wi-Fi-hálózatra kell csatlakoztatni, és a böngészőnek fel kell tudnia oldani a `.local` neveket. Egy egycsatornás és egy kétcsatornás boardot támogat; több azonos típusú boardhoz külön címzés szükséges. A nyitóoldal IP-címmel vagy a saját AP `192.168.4.1` címén is működik a helyi boarddal, de a másik board csak akkor nyitható meg, ha a böngészőt futtató eszköz arról a hálózatról eléri. Kikapcsolt vagy nem elérhető másik board nem akadályozza a helyi vezérlést. A panelek saját **külön megnyitása** hivatkozása az adott vezérlést önálló oldalon nyitja meg; közvetlenül a `/control` útvonalon is elérhető.

## Fordítás és feltöltés

```sh
pio run -e aqualight
pio run -e aqualight -t upload
pio device monitor
python3 test_controller.py
python3 test_maintenance.py
python3 test_diagnostics.py
python3 test_sensor_path.py
python3 test_http.py
python3 test_roles.py
python3 test_board_link.py
python3 test_web_assets.py
python3 test_mesh_config.py
python3 test_button_policy.py
python3 test_local_ota_stream.py
python3 test_usb_status.py
python3 test_ota.py
node test_ui.cjs
```

A PlatformIO platform és az AHTxx-könyvtár verziója rögzített. Az USB-s napló 115200 baudon érhető el. A natív teszt C++17-fordítót és annak AddressSanitizer/UBSan támogatását használja; nem igényel külön tesztcsomagot.

## Korábbi első használat – 1.2.x

1. A soros naplóban megjelenik az `AquaLight-…` Wi-Fi neve és jelszava. A kétcsatornás build első induláskor eszközönként különböző jelszót generál, amely újraindításkor megmarad. Az egycsatornás build a Gitből kizárt, `0600` jogosultságú `.ota-credentials.json` helyi `1ch` bejegyzéséből generált privát fejlécet használja; ugyanez a jelszó védi a saját AP-t, a helyi OTA-t és a Telnetet.
2. Csatlakozz ehhez a hálózathoz, és nyisd meg a `http://192.168.4.1/` címet. Az ESP akkor is kiszolgálja az oldalt, ha nincs internet.
3. A hálózati beállításoknál opcionálisan add meg az otthoni Wi-Fi adatait. Csatlakozás után a naplóban jelzett IP-címen, illetve mDNS-t támogató eszközön a kétcsatornás board a `http://led-control.local/`, az egycsatornás a `http://led-control-1ch.local/` címen is elérhető. A közös nyitóoldalon a helyi board paneljében állítsd be a hálózatot. A saját Wi-Fi tartalék hozzáférésként megmarad.
4. Állítsd be az órát a telefon/böngésző idejéről, vagy internetkapcsolat mellett várd meg az NTP-szinkront. Az óra magyar téli–nyári időszámítást használ.
5. A napi grafikonon válaszd ki a teljes spektrumú vagy hideg fehér csatornát. Telefonon alapból a grafikonon is görgethetsz; a pontok húzásához kapcsold be a **Pontok mozgatása** gombot, majd görgetéshez kapcsold ki. Egérrel közvetlenül húzhatod a pontokat. Dupla koppintással új pontot adhatsz hozzá. A **Visszavonás** az utolsó szerkesztést állítja vissza, egy teljes húzást egyetlen lépésként; legfeljebb 40 lépést őriz, az oldal újratöltéséig vagy a program sikeres mentéséig. Megszakított húzáskor a pont visszaáll a kezdőértékre. A változások csak a Mentés után kerülnek a lámpára; ezután válts automata módra. A kézi vezérlés külön lenyitható részben érhető el; külön gombbal kapcsolható be, kikapcsolása automata módra tér vissza. A csúszkák csak bekapcsolt kézi módban állíthatók.

Új eszközön a lámpa kikapcsolt módban indul. A szerkeszthető mintaprogram 08:00–09:00 között 0-ról 50%-ra emel mindkét csatornán, 17:00-ig tartja, majd 18:00-ig lecsökkenti 0-ra. Mentése önmagában nem kapcsol automata módra.

Mindkét csatorna saját 2–16 egyedi időpontból álló görbét tárol; a teljes spektrumú csatorna pontjainak szerkesztése nem változtatja meg a hideg csatorna időpontjait vagy fényerejét. A csatornák a saját pontjaik között folyamatosan, lineárisan változnak, az utolsó ponttól az elsőig éjfélen át is. A helyi óra tavaszi előreállásakor az átugrott óra kimarad; ősszel az ismétlődő helyi óra megismétli a görbe adott szakaszát.

Az 1.2.1-es Wi-Fi-helyreállítás öt perc folyamatos hálózatkimaradás és legalább két lejárt csatlakozási kísérlet után legfeljebb két automatikus újraindítást enged. A felhasznált keretet az NVS őrzi, ezért újraindítás vagy áramtalanítás nem nullázza. A keret csak tíz perc folyamatos stabil Wi-Fi-kapcsolat után áll vissza. A két újraindítás után a normál újracsatlakozás és a lámpavezérlés tovább fut; nincs további helyreállítási reboot. Aktív saját AP-kliens, Wi-Fi-beállításváltás, OTA-művelet vagy mentési hiba halasztja az újraindítást. A vezérlő újraindítása rövid fénykimaradást okozhat, teljesen folytonos PWM-et nem garantál.

Kizárólag a saját Wi-Fi-helyreállítási szoftveres újraindításakor, előzőleg érvényes óra esetén őrzi meg a rendszer a tovább járó RTC-időt. Így az automata program internet nélkül is folytatható. Áramszünet és más indítás továbbra is új időszinkront igényel; NVS-be mentett régi dátumot nem állít vissza. Az RTC szoftveres újraindításon át történő időmegőrzését az [Espressif rendszeridő-dokumentációja](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-reference/system/system_time.html) írja le; a tényleges újraindítást és időfolytonosságot később hardveren is ellenőrizni kell.

A saját AP elérhetőségéért az állomáscsatlakozásnak 15 másodperces határideje van. Sikertelen próbálkozás után 30, 60, majd legfeljebb 120 másodperc szünet következik, az AP kikapcsolása nélkül; sikeres csatlakozás visszaállítja a rövid várakozást. A natív, késleltetés nélküli automatikus újracsatlakozás ki van kapcsolva. A hálózati napló jelszavak nélkül jelzi a hibakódot, az utolsó jelerősséget, az AP csatornáját és a következő próbálkozást. Az ESP32-C3 SuperMini rádiójának alapkalibrációja 8,5 dBm; más boardhoz a `LED_WIFI_TX_POWER` fordítási makróval változtatható, például a natív `WIFI_POWER_19_5dBm` értékre. A tényleges beállított teljesítmény a naplóban visszaolvasható.

A mód, a program és a kézi fényerő megmarad újraindításkor. A kézi csúszkák beállítása az utolsó módosítás után 2 másodperccel kerül flashbe; a program és a módváltás azonnal mentődik. Áramszünet után automata módban a kimenetek érvényes új időszinkronig kikapcsolva maradnak. A rendszer nem állít vissza egy régi, elmentett dátumot. Kézi módban óra nélkül is világít a beállított értékkel.

Az AHT20 hibája a lámpavezérlést nem állítja le. A hibás mérés helyett a felület érvénytelen adatot jelez, és az ESP újrapróbálja a szenzor inicializálását. PWM-hiba esetén mindkét kimenet kikapcsolt állapotba kerül.

### Terrárium mérési naplója

A kétcsatornás Wi-Fi-s terrárium élő mérései alatt az **Utolsó 48 óra mérései (CSV)** hivatkozás tölti le a naplót. A közvetlen végpont a `GET /api/sensor-history.csv`. A fájl időrendben tartalmazza az utolsó 48 óra lezárt, UTC szerint igazított tízperces ablakait, amelyekhez van érvényes mérési pár. Az oszlopok az ablak kezdete (ISO 8601 UTC), a hőmérséklet–páratartalom párok száma, a hőmérséklet átlaga és populációs szórása (°C), majd a relatív páratartalom átlaga és populációs szórása (%). A CSV vesszővel tagol, a tizedesjel pont.

Az óraszinkron után az AHT20 meglévő, öt másodpercenkénti mintavétele szolgáltatja az adatokat. Egy hibás mérési pár kimarad. Egy ablakban egyetlen érvényes pár esetén a szórás nulla; üres ablakhoz nem készül sor. Az aktuális ablak RAM-ban gyűlik, ezért újraindításkor elveszhet. Visszafelé állított óra esetén a folyamatban lévő ablak mérései addig szünetelnek, amíg az idő ismét el nem éri annak kezdetét. Érvénytelen eszközidőnél vagy elérhetetlen naplótárolónál a végpont HTTP 503-at ad.

A lezárt ablakokat a meglévő 128 KiB-os `spiffs` partíció LittleFS-ként tárolja, 49, körforgásban használt fájlban. Egy fájl egy UTC-óra legfeljebb hat rekordját tartalmazza. A fájlok ellenőrzőösszeggel védett rekordokat tartalmaznak, és az órafájl cseréje csak az új példány lemezre írása után történik. Első indításkor csak a teljesen üres partíció formázódik. A teljes flash törlése a naplót is törli.

A szenzorfeladat a lezárt összesítést egy nyolc rekordos RAM-sorba adja. Az újraindítás a nyitott ablak mellett a még kiírásra váró rekordokat is elveszítheti. A HTTP-kiszolgáló részletekben olvassa és küldi a CSV-t, így a teljes exporthoz nem kell külön RAM-puffer.

**Ellenőrzés és telepítési állapot.** Az `aqualight` és `aqualight1ch` PlatformIO-build sikeres volt. A `python3 test_sensor_history.py` a statisztikát, az időablakokat, a 48 órás határt, a fájlok körforgását és a sérült vagy félbeszakadt írást vizsgálja gazdagépen. A HTTP-, szenzorútvonal- és közös felületi tesztek is sikeresek voltak. A naplózó build még nem került a terráriumra. Fizikai flashírást és áramkimaradást ezen a builden még nem próbáltunk. A külön BLE-böngészőteszt futtatásához hiányzott a `ble-web/ble_transport.js` fájl.

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

Az alkalmazás verziója a `LED_CONTROL_VERSION` fordítási makró; az aktuális forrásverzió **1.3.2**. A masteren USB-n és HTTP-n visszaigazolt **1.3.2** fut. A slave-re az **1.3.0** OTA-telepítése és kezdeti ESP-NOW-működése igazolt; később a peer-kapcsolat megszűnt. Újraindítása után a slave hotspotja ismét látható, a tartós közös működés még nincs igazolva. Mindkét 1.3.2 változat lefordult, a slave 1.3.2 képe még nincs telepítve. Az Androidos BLE-pilot külön környezetben elkészült (1.4.0-ble), de a masterre telepítése még nincs igazolva; részletei a fenti BLE-próba szakaszban szerepelnek. A részleteket a [telepítési jelentés](diagnostics/1.3.0-implementation-report.md) rögzíti. Az `update` és `maintenance` állapotmezők jelszó nélkül jelzik a frissítés állapotát. A `min_spiffs.csv` partíciós tábla két `0x1e0000` méretű alkalmazáshelyet használ; az NVS címe/mérete (`0x9000`, `0x5000`) és az `otadata` (`0xe000`, `0x2000`) megmarad. A partíciós tábla egyszeri telepítése USB-n történik, az NVS törlése nélkül. Ezután az OTA csak az inaktív alkalmazáshelyet írja.

A webes frissítés gomb külön, nyilvános Firebase-kiadási csatornán keres új firmware-t. A kiadás aláírt manifesztből és alkalmazásképből áll; az otthoni Wi-Fi adatai az eszközön maradnak, a fordított firmware azonban tartalmazhat helyi hitelesítő adatot, ezért eszközre szabott buildet nem szabad nyilvános firmware-csatornára feltölteni. A kiadási cél a BeeScale által is használt `terrarium1` Firebase-projekt külön `led-control-terrarium1` Hosting-oldala. A kétcsatornás csatorna címe `https://led-control-terrarium1.web.app/channels/stable.json`, az egycsatornásé `https://led-control-terrarium1.web.app/channels/stable-1ch.json`. A meglévő BeeScale-oldal és a CafePlant projekt változatlan. A vezérlő indulás után 60 másodperccel, majd sikeres ellenőrzések után 30 percenként keres kiadást; a webes gombbal korábban is indítható keresés. A hálózati letöltés külön munkaszálon fut. A beállításmentés befejezése előtt nincs szándékos újraindítás; az új felhős kép indulási állapotát a vezérlő ellenőrzi.

2026. november 16-án mindkét boardra helyi, hitelesített ArduinoOTA-val került az **1.2.0** firmware: négyzetes fényerőskálával, 13 bites PWM-mel, mobilos pontmozgatás-kapcsolóval, visszavonással és a régebbi/azonos verziójú felhős képek elleni védelemmel. Újraindulás után mindkét board az új verziót és az új kezelőfelületet szolgálta ki; a mentett programok, üzemmódok és kézi értékek megmaradtak, a PWM-, szenzor- és tárolóállapot rendben volt. Ez a helyi OTA és a szoftveres állapot ellenőrzése; a fényerőgörbe tényleges megítélése a lámpán történik. A felhős csatornák továbbra is az alábbi korábbi kiadásra mutatnak.

Ugyanezen a napon mindkét boardra helyi ArduinoOTA-val felkerült az **1.2.1**: csatornánként kalibrált minimumfény, a **terrárium** és **led-szallag** nevek, a lila teljes spektrumú csatorna helyes felirata, valamint a legfeljebb két újraindítást engedő Wi-Fi-helyreállítás. Az új verziót és a helyes neveket mindkét eszköz kiszolgálja, Wi-Fi-, PWM-, szenzor- és tárolóállapotuk rendben van. A kiesést kiváltó hardveres helyreállítási próbát ez a telepítés nem helyettesíti.

A terráriumra ezután külön felkerült az **1.2.2**, amely a korábbi 1%-os PWM-et az 5%-os álláshoz rendeli, és az 1–5%-os tartományt finomítja. A mentett OTA-jelszóval történt feltöltés után az eszköz `healthy` indulási állapotot jelzett; a program, mód és kézi értékek megmaradtak, a Wi-Fi-, PWM-, szenzor- és tárolóállapot rendben volt. A led-szallag változatlanul az **1.2.1** firmware-t futtatja.

A felhasználó által jóváhagyott **1.2.3-as, 200 Hz-es próbaverzió** szintén felkerült a terráriumra helyi OTA-val. Az új verzió `healthy` állapotban indult, a program, mód és Wi-Fi megmaradt, a PWM-, szenzor- és tárolóállapot rendben volt. A kézi fényértékek a feltöltés alatti/utáni ellenőrzés során eltértek az induláskori pillanatképtől; ezekre nem történt visszaírás. A halvány fény és vibrálás értékelése a felhasználó helyszíni próbája.

2026. november 17-én külön jóváhagyással felkerült a terráriumra az **1.2.4-es, 200 Hz-es, 14 bites kalibrációs próba**. Az új verzió `healthy` indulási állapotot jelzett; a program, mód és kézi értékek megmaradtak, a Wi-Fi-, PWM-, szenzor- és tárolóállapot rendben volt. Az ötödnyi optikai fény célját ez nem igazolja, a fényt a felhasználó hasonlítja össze. A led-szallagra nem történt új feltöltés.

2026. november 17-én a felhasználó visszajelzése alapján a 14 bites próbát felváltotta az **1.2.5** a terráriumon: visszaállt az 1000 Hz-es, 13 bites, 5%-nál bekapcsoló fő-LED-görbe. A helyi OTA után `healthy` állapotot jelzett; a program, üzemmód és kézi értékek megmaradtak, a Wi-Fi-, PWM-, tároló- és szenzorállapot rendben volt.

A választófüles közös felület ellenőrzött felhős kiadásai (2026. november 13., firmware-forrás: `558aab6`):

| Board | Kiadási azonosító | Csatornafájl |
| --- | --- | --- |
| Kétcsatornás | `led-control-1.1.0-tabs-558aab6` | `stable.json` |
| Egycsatornás | `1ch-1.1.0-tabs-558aab6` | `stable-1ch.json` |

Mindkét board a felhőből telepítette a saját kiadását, újraindult, majd az `/api/state` válaszában a megfelelő `update.release_id` mellett `up_to_date` állapotot jelzett. A PWM, a szenzor, a tároló és az óra állapotjelzői rendben voltak; a mentett programok és kézi fényerők megmaradtak. A közös oldalon mindkét board élő felülete betöltődött a saját fülén, és a felhasználó is visszaigazolta a működést. Ez az OTA-telepítést és a webes integrációt igazolja, a PWM-jel elektromos mérését nem helyettesíti.

A kijelzett alkalmazásverzió mindkét kiadásban `1.1.0`; az OTA a futó kép hashét hasonlítja össze a manifeszttel, ezért azonos verziószám mellett is telepíti az eltérő képet. A konkrét felhős kiadást az `update.release_id` azonosítja a következő sikeres felhős ellenőrzés után. **Az 1.2.0-tól** eltérő képet csak szigorúan újabb, számszerű `MAJOR.MINOR.PATCH` verzióval telepít a felhős frissítő. A régebbi vagy azonos verziójú csatorna nem írja felül a helyi OTA-val telepített firmware-t. Az `update.release_id` a csatorna kiadását jelzi; helyi feltöltés után a futó firmware verzióját a `maintenance.version` mutatja.

A privát aláírókulcs dokumentált helye a repositoryn kívüli `~/.config/led-control/ota-signing-key.pem`, `0600` jogosultsággal. A 2026. november 16-i helyi frissítéskor ez a fájl nem áll rendelkezésre ezen a gépen; a felhős kiadáshoz az eredeti kulcs szükséges, a helyi ArduinoOTA az eszköz saját jelszavával használható. A firmware-be és Gitbe csak a `src/ota_public_key.h` nyilvános ellenőrzőkulcsa kerül. A kiadóeszköz OpenSSL 3.2 vagy újabb verziót használ; alapértelmezése dry run, fájlírás és publikálás nélkül.

```sh
python3 release_firmware.py --firmware .pio/build/aqualight/firmware.bin \
  --release-id led-control-1.1.0-tabs-558aab6 --version 1.1.0
python3 release_firmware.py --board 1ch --firmware .pio/build/aqualight1ch/firmware.bin \
  --release-id 1ch-1.1.0-tabs-558aab6 --version 1.1.0
python3 release_firmware.py --firmware .pio/build/aqualight/firmware.bin \
  --release-id led-control-1.1.0-tabs-558aab6 --version 1.1.0 --prepare \
  --output "$HOME/.local/share/led-control/hosting"
python3 release_firmware.py --board 1ch --firmware .pio/build/aqualight1ch/firmware.bin \
  --release-id 1ch-1.1.0-tabs-558aab6 --version 1.1.0 --prepare \
  --output "$HOME/.local/share/led-control/hosting"
firebase deploy --project terrarium1 \
  --config "$HOME/.local/share/led-control/hosting/firebase.json" \
  --only hosting --non-interactive
```

A `--prepare` csak a külön, repositoryn kívüli Hosting-kimenetet készíti elő. Mindkét board kiadását ugyanabba a Hosting-kimenetbe készítsd elő, majd egyetlen deployjal publikáld a két csatornát. Kizárólag az utolsó, külön kiadott Firebase-parancs publikál; annak konfigurációja a dedikált `led-control-terrarium1` site-ot nevezi meg. Korábbi kiadások megmaradnak, azonos kiadásazonosító eltérő képpel nem írható felül; új firmware-hez új kiadási azonosítót használj. Már OTA-képes boardnál ehhez nem kell USB: a saját vezérlőpanel **Frissítés és diagnosztika → Frissítés keresése** gombja elindítja a letöltést és a telepítést. Újraindulás után töltsd újra a böngészőoldalt is az új felület betöltéséhez.

Helyi, hitelesített **ArduinoOTA** a masteren `led-control.local:3232`, a slave karbantartási hotspotjára csatlakozva pedig `192.168.4.1:3232` címen érhető el. Jelszava az eszköz saját AP-jelszava; nem kerül API-válaszba vagy Telnet-naplóba. Csak alkalmazásképet tölts fel (`firmware.bin`), ne teljes flashképet vagy fájlrendszerképet. A helyi feltöltő a telepített Arduino **3.3.8** `espota.py` PBKDF2-HMAC-SHA256 hitelesítését használja.

Példa a már elkészített master alkalmazáskép ellenőrzésére. A tényleges feltöltéshez add hozzá a `--upload` kapcsolót:

```sh
python3 tools/upload_local_ota.py --board 2ch --ip led-control.local \
  --image .pio/build/aqualight/firmware.bin \
  --snapshot diagnostics/before-master-ota.json
```

A helyi OTA-jelszavak a felhasználó kérésére a repository `.ota-credentials.json` rejtett fájljában vannak elmentve, `0600` jogosultsággal, Gitből kizárva. Ez az eszközök saját AP-/OTA-jelszavait tárolja; az otthoni hálózat jelszava továbbra is az eszközök NVS-tárolójában marad.

A helyi és felhős OTA közös zárral védi a flashírást. Helyi feltöltés előtt a függő kézi beállítások mentődnek; mentési hiba esetén a feltöltés megszakad. Sikeres helyi feltöltés után az újraindítás szintén a biztonságos mentési állapotra vár.

## Telnet diagnosztika

A `telnet led-control.local 23` (a slave karbantartási hotspotján `telnet 192.168.4.1 23`) egyetlen klienssel használható, az adott board saját AP-jelszavával. A belépés 10 másodperc után lejár, legfeljebb három hibás próbálkozás engedélyezett; ezután 30 másodperces szünet következik. Az inaktív munkamenet 5 perc után bezárul. A Telnet titkosítatlan helyi protokoll, ezért megbízható LAN-on/AP-n használd; nem általános parancsértelmező és nem tükrözi a soros naplót.

| Parancs | Művelet |
| --- | --- |
| `help` | Engedélyezett parancsok |
| `status` | Olvasható világítás-, szenzor-, tároló- és frissítési pillanatkép, jelszavak nélkül |
| `ota-check` | Aláírt felhős frissítés keresésének sorba állítása |
| `reboot` | Újraindítás a függő beállítások sikeres mentése után |

A parser legfeljebb 96 karakteres sorokat, CRLF/CR-NUL lezárást és alap Telnet IAC-egyeztetést kezel. A bemenet, kimeneti sor és a ciklusonkénti munka korlátozott; nem olvasó kliens nem fogja meg a vezérlést. A `python3 test_maintenance.py` az éles parser/beléptetés kódját ASan/UBSan mellett ellenőrzi; TCP-t, OTA-átvitelt, flashváltást és újraindítást hardveren kell ellenőrizni.

## Licenc

MIT – lásd a [LICENSE](LICENSE) fájlt.

A `diagnostics/` könyvtár, a naplók, hálózati mentések, firmware-/flashképek, helyi telepítési metaadatok és hitelesítő fájlok Gitből kizártak. A fejlesztői forráskód és a mesterséges tesztadatok verziókezelhetők; valódi eszközazonosítót és hitelesítő adatot ne helyezz ezekbe. Az egycsatornás helyi jelszót a `tools/generate_device_config.py` buildlépés kezeli, a privát generált fejléc a kizárt buildkönyvtárban marad.
