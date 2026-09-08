# Matching core: C++ build и gRPC contract boundary

Этот boundary относится к Architecture Reset / Processing v2 и проверяет реальную
границу между Python `careerops-processing` и отдельным C++20 сервисом
`careerops-matching-core`.

Канонический wire contract находится только в
`proto/careerops/matching_core/v1/control.proto`. Сгенерированный код не хранится в Git и
не является вторым источником истины.

## Что доказывает C++ build gate

Job `Matching core / C++ build` использует обычный CMake path сервиса и те же классы
системных build-зависимостей, что production Dockerfile. Gate обязан:

- сконфигурировать `services/careerops-matching-core` в Release;
- сгенерировать C++ protobuf и gRPC sources из канонического `control.proto`;
- собрать `careerops-matching-core` с текущими `-Wall -Wextra -Wpedantic -Werror`;
- проверить наличие четырёх generated C++ файлов;
- проверить наличие исполняемого бинарника;
- проверить через `ldd`, что у бинарника нет unresolved shared libraries;
- передать ровно этот собранный бинарник следующему contract job как CI artifact.

Таким образом изменение `.proto`, CMake или C++ реализации не может пройти CI, если
production executable больше не собирается из текущего дерева репозитория.

## Что доказывает gRPC contract gate

Pytest boundary помечен `integration_matching_core`. Он не входит в FAST suite.
Dedicated job:

1. скачивает бинарник, собранный предыдущим C++ gate;
2. устанавливает отдельный optional extra `matching-core-test`;
3. во временный каталог генерирует Python protobuf/gRPC stubs из того же канонического
   `control.proto`;
4. запускает настоящий C++ executable на случайном loopback-порту;
5. Python stub вызывает `Health` и `GetCapabilities` по настоящему gRPC;
6. проверяет service name, service version, protocol version, method set и текущие
   capability flags;
7. запускает встроенный C++ `--healthcheck` против того же сервера.

Текущий control contract:

- service: `careerops.matching_core.v1.MatchingCoreControl`;
- methods: `Health`, `GetCapabilities`;
- service name: `careerops-matching-core`;
- service version: `0.1.0`;
- protocol version: `careerops.matching-core.control.v1`;
- `evaluate_match_available = false`;
- `evaluate_batch_available = false`.

Последние два флага намеренно остаются `false`, пока финальный evaluation wire contract не
зафиксирован. Тест не публикует временный EvaluateMatch API.

## Почему Python client не добавляется production-кодом в этой задаче

В текущем Processing v2 есть `matching_core_target`, но production Python gRPC client ещё не
реализован. Эта задача является test reset, а не реализацией следующего runtime protocol.
Поэтому Python stubs генерируются только внутри integration test из канонического proto.
Это уже ловит wire drift между Python и C++, не создавая преждевременный production API.

Когда production client появится, этот же boundary должен быть переведён с тестового stub
на реальный client adapter без ослабления C++ build gate.

## Локальный запуск

На Debian/Ubuntu с системными gRPC/Protobuf build packages:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  build-essential cmake libgrpc++-dev libprotobuf-dev pkg-config \
  protobuf-compiler protobuf-compiler-grpc

cmake -S services/careerops-matching-core -B build/matching-core \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build/matching-core --parallel

python -m pip install -e ".[dev,matching-core-test]"
CAREEROPS_MATCHING_CORE_BINARY="$PWD/build/matching-core/careerops-matching-core" \
  python -m pytest -q -m "integration_matching_core" \
  tests/integration/matching_core
```

FAST selection обязана явно исключать оба реализованных внешних boundary:

```bash
python -m pytest -q \
  -m "not integration_postgres and not integration_matching_core"
```

Отсутствующий C++ binary или несовместимый proto не должны превращаться в skip: dedicated
contract job обязан падать.
