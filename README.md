# AquaLight

ESP32-C3 SuperMini akváriumvilágítás két fehér LED-szalaggal és AHT20-szenzorral. A teljes webes felület az ESP-n található; nem használ külső JavaScriptet vagy felhős szolgáltatást.

| Jel | GPIO |
| --- | --- |
| Meleg fehér, 2700 K | 0 |
| Hideg fehér, 6500 K | 4 |
| AHT20 SDA | 1 |
| AHT20 SCL | 3 |

A PWM alapértéke 1 kHz, 10 bit, aktív magas vezérlés. A polaritás a `src/main.cpp` elején csatornánként változtatható.

## Fordítás és feltöltés

```sh
pio run -e aqualight
pio run -e aqualight -t upload
pio device monitor
python3 test_controller.py
node test_ui.cjs
```

A PlatformIO platform és az AHTxx-könyvtár verziója rögzített. Az USB-s napló 115200 baudon érhető el. A natív teszt C++17-fordítót és annak AddressSanitizer/UBSan támogatását használja; nem igényel külön tesztcsomagot.

## Első használat

1. A soros naplóban megjelenik az `AquaLight-…` Wi-Fi neve és az első induláskor generált, eszközönként különböző jelszó. A jelszó újraindításkor megmarad.
2. Csatlakozz ehhez a hálózathoz, és nyisd meg a `http://192.168.4.1/` címet. Az ESP akkor is kiszolgálja az oldalt, ha nincs internet.
3. A hálózati beállításoknál opcionálisan add meg az otthoni Wi-Fi adatait. Csatlakozás után a naplóban jelzett IP-címen, illetve mDNS-t támogató eszközön a `http://akvarium.local/` címen is elérhető. A saját Wi-Fi tartalék hozzáférésként megmarad.
4. Állítsd be az órát a telefon/böngésző idejéről, vagy internetkapcsolat mellett várd meg az NTP-szinkront. Az óra magyar téli–nyári időszámítást használ.
5. A napi grafikonon válaszd ki a meleg vagy hideg csatornát. A pontok húzásával módosíthatod az időt és a fényerőt, dupla koppintással új pontot adhatsz hozzá. A változások csak a Mentés után kerülnek a lámpára; ezután válts automata módra. A kézi vezérlés külön lenyitható részben érhető el.

Új eszközön a lámpa kikapcsolt módban indul. A szerkeszthető mintaprogram 08:00–09:00 között 0-ról 50%-ra emel mindkét csatornán, 17:00-ig tartja, majd 18:00-ig lecsökkenti 0-ra. Mentése önmagában nem kapcsol automata módra.

Mindkét csatorna saját 2–16 egyedi időpontból álló görbét tárol; a meleg pontok szerkesztése nem változtatja meg a hideg csatorna időpontjait vagy fényerejét. A csatornák a saját pontjaik között folyamatosan, lineárisan változnak, az utolsó ponttól az elsőig éjfélen át is. A helyi óra tavaszi előreállásakor az átugrott óra kimarad; ősszel az ismétlődő helyi óra megismétli a görbe adott szakaszát.

A mód, a program és a kézi fényerő megmarad újraindításkor. A kézi csúszkák beállítása az utolsó módosítás után 2 másodperccel kerül flashbe; a program és a módváltás azonnal mentődik. Áramszünet után automata módban a kimenetek érvényes új időszinkronig kikapcsolva maradnak. A rendszer nem állít vissza egy régi, elmentett dátumot. Kézi módban óra nélkül is világít a beállított értékkel.

Az AHT20 hibája a lámpavezérlést nem állítja le. A hibás mérés helyett a felület érvénytelen adatot jelez, és az ESP újrapróbálja a szenzor inicializálását. PWM-hiba esetén mindkét kimenet kikapcsolt állapotba kerül.

## Helyi API

A `GET /api/state` az aktuális állapotot adja vissza JSON-ként, jelszavak nélkül. A `schedule` mező két külön listát tartalmaz: `{"warm":[{"minute":480,"level":0},…],"cool":[{"minute":510,"level":0},…]}`. A módosító kérések `POST`, `application/x-www-form-urlencoded` tartalommal és `X-Aqua-Request: 1` fejléccel működnek. A fejléc a más weboldalakról indított böngészős kéréseket korlátozza; a hálózaton lévő kliensek számára nem helyettesít felhasználói hitelesítést. A vezérlőt saját, megbízható helyi hálózaton használd, porttovábbítás nélkül.

| Végpont | Mezők |
| --- | --- |
| `/api/mode` | `mode=off`, `manual` vagy `auto` |
| `/api/manual` | `warm=0..100`, `cool=0..100`; kézi módra vált |
| `/api/program` | `warmPoints=perc,fényerő;perc,fényerő;…` és `coolPoints=perc,fényerő;perc,fényerő;…`; csatornánként 2–16 növekvő, egyedi percérték 0–1439 között |
| `/api/time` | `epoch=` UTC Unix-másodperc, 2024–2099 között |
| `/api/wifi` | `ssid=`, `password=`; a jelszó üres nyílt hálózatnál, egyébként 8–63 bájt |

A sikeres válasz `{"ok":true}`. Hiba esetén a válasz HTTP-hibakódot és `{"ok":false,"error":"…"}` objektumot tartalmaz. A program mentése atomikus: ha bármelyik csatorna görbéje érvénytelen, egyik aktív görbe sem változik meg. A Wi-Fi-mentés sikere a csatlakozási kísérlet indítását jelzi; a tényleges kapcsolat az állapotból derül ki. A kikapcsolás tárolóhiba esetén is végrehajtódik, bár a mentés hibát jelezhet.

A hostteszt a két független görbét, az atomikus kettős validálást, a parsereket, módokat, PWM-számítást, időzítést és az időzóna váltásait ellenőrzi. A valódi PWM-jelet, fényerőt, szenzort, HTTP-kiszolgálást, Wi-Fi-t és flashbe mentést hardveren kell ellenőrizni.

A `node test_ui.cjs` a tényleges beágyazott JavaScriptet futtatja egyszerű DOM- és hálózati modellel. A független görbéket, húzást, dupla koppintást, pontkorlátokat, fókuszt és a mentés/módváltás versenyhelyzeteit ellenőrzi; a valódi böngészős érintéskezelést és megjelenést nem bizonyítja.
