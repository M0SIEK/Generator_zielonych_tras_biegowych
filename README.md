# 🌿 Generator zielonych tras spacerowych i biegowych (Opole)

Aplikacja webowa działająca w trybie offline, służąca do wyznaczania zoptymalizowanych tras pieszych i biegowych na terenie Opola (w promieniu 5 km od centrum). System pozwala płynnie balansować między najkrótszym dystansem a maksymalizacją udziału terenów zielonych i unikaniem ruchliwych ulic.

**Autorzy:** Patryk Moś, Jakub Neugebauer  
**Przedmiot:** Analiza danych przestrzennych (Politechnika Opolska, 2026/2027)  
**Prowadzący:** dr inż. Krzysztof Smykała

---

## 🛠️ Technologie i architektura

* **Backend:** `Python`, `FastAPI`, `Uvicorn`
* **Analiza danych przestrzennych i grafowych:** `Pyrosm`, `OSMnx`, `GeoPandas`, `NetworkX`, `PyProj`
* **Frontend:** `HTML5`, `CSS3`, `JavaScript`, `Leaflet.js`

### Zasada działania
1. Przy starcie serwera plik `.osm.pbf` jest przycinany do obszaru Opola, a sieć piesza jest rzutowana na lokalny metryczny układ współrzędnych (UTM).
2. Ekstrahowane są strefy zieleni (bufor 25 m) oraz strefy hałasu (bufory 20–60 m zależne od klasy drogi).
3. Za pomocą operacji *spatial join* każdej krawędzi grafu przypisywany jest ułamek pokrycia zielenią i hałasem.
4. Algorytm **A\*** z dopuszczalną heurystyką euklidesową wyznacza trasę optymalną na podstawie suwaka preferencji użytkownika ($g \in [0, 1]$) oraz porównuje ją z trasą najkrótszą.

---

## 🚀 Instalacja i uruchomienie

### 1. Wymagania wstępne
Ze względu na limit rozmiaru plików na GitHubie (100 MB), plik mapy województwa opolskiego nie jest dołączony do repozytorium.
* Pobierz plik `opolskie-latest.osm.pbf` z serwisu [Geofabrik](https://download.geofabrik.de/europe/poland/opolskie.html).
* Zmień jego nazwę na **`opolskie.osm.pbf`** i umieść w folderze `backend/`.

### 2. Konfiguracja środowiska i start serwera
```bash
# Utworzenie i aktywacja środowiska wirtualnego
python -m venv .venv
.venv\Scripts\activate  # Windows

# Instalacja zależności
pip install -r requirements.txt

# Uruchomienie serwera z folderu backend
cd backend
uvicorn main:app --reload
