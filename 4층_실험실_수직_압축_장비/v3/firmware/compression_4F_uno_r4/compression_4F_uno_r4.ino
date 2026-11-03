// =====================================================================
//  4층 실험실 수직 압축 장비 펌웨어 (v3)
//  Arduino UNO R4 Minima + A4988 + 14HR08-0654S
//
//  직렬 115200 bps, 줄 끝 '\n'
//    start,<mm>,<mm/s>   자동 이동 (양수 = 하강 = 시편을 누르는 방향)  → 완료 시 "DONE"
//    JU,<mm/s>           조그 하강 (누르고 있는 동안)
//    JD,<mm/s>           조그 상승
//    STOP                감속 정지. 드라이버는 여자 상태 유지(위치 고정)   → "STOPPED"
//    MOFF                즉시 정지 + 드라이버 전원 차단 (축 자유)          → "MOTOR_OFF"
//    MON                 드라이버 전원 인가                               → "MOTOR_ON"
//
//  ※ 모터 전원 OFF(MOFF)가 실제로 동작하려면 A4988의 EN 핀을 D4에 연결해야 한다.
//    현재 배선처럼 EN이 비어 있으면(내부 풀다운 → 항상 활성) MOFF는 정지만 하고
//    전원 차단은 되지 않는다. 수직축이므로 EN을 끊으면 헤드가 자중으로 내려올 수 있다.
// =====================================================================
#include <AccelStepper.h>
#include <stdio.h>

#define DIR_PIN     2
#define STEP_PIN    3
#define ENABLE_PIN  4    // A4988 EN (LOW = 활성). 배선하지 않으면 무해.

const float STEPS_PER_MM   = 800.0;   // 실측 확인됨
const float MAX_SPEED_SPS  = 4000.0;
const float ACCEL_SPS2     = 2000.0;

// DIAG,ON / DIAG,OFF: 선택형 진단. 구동 알고리즘은 그대로 유지한다.
// STEP HIGH 출력 호출 직후의 소프트웨어 시각이며 핀 전압/실제 축의 계측값은 아니다.
// 상세 STEP 기록은 설정 속도 8 STEP/s(0.01 mm/s) 이하에만 적용한다.
struct DiagEvent {
  char type;
  uint32_t at, sequence;
  int32_t position;
  char detail[48];
};
const uint8_t DIAG_CAPACITY = 48;
DiagEvent diagEvents[DIAG_CAPACITY];
uint8_t diagHead = 0, diagCount = 0;
bool diagEnabled = false;
uint32_t diagSequence = 0, diagDropped = 0, diagSkipped = 0, diagLastStatus = 0;
char diagTx[112];
size_t diagTxLength = 0;

void queueDiag(char type, uint32_t at, int32_t position, const char *detail) {
  if (diagCount == DIAG_CAPACITY) {
    ++diagDropped;  // 전송 때문에 모터를 기다리게 하지 않는다. 누락은 상태에 보고한다.
    return;
  }
  DiagEvent &event = diagEvents[(diagHead + diagCount) % DIAG_CAPACITY];
  event.type = type;
  event.at = at;
  event.sequence = diagSequence;
  event.position = position;
  snprintf(event.detail, sizeof(event.detail), "%s", detail);
  ++diagCount;
}

class TraceStepper : public AccelStepper {
public:
  TraceStepper() : AccelStepper(AccelStepper::DRIVER, STEP_PIN, DIR_PIN) {}
protected:
  void setOutputPins(uint8_t mask) override {
    const bool high = (mask & 1) != 0;
    const bool falling = stepHigh && !high;
    AccelStepper::setOutputPins(mask);
    if (diagEnabled && high && !stepHigh) edgeAt = micros();
    stepHigh = high;
    if (diagEnabled && falling) {
      ++diagSequence;
      if (maxSpeed() <= 8.001f) {
        char interval[16];
        snprintf(interval, sizeof(interval), "%lu", (unsigned long)_stepInterval);
        queueDiag('S', edgeAt, currentPosition(), interval);
      } else {
        ++diagSkipped;  // 고속에서는 개별 펄스 대신 누적 개수만 상태 보고한다.
      }
    }
  }
private:
  bool stepHigh = false;
  uint32_t edgeAt = 0;
};

TraceStepper stepper;

void queueDiagStatus(char type) {
  char counts[32];
  snprintf(counts, sizeof(counts), "%lu:%lu", (unsigned long)diagDropped, (unsigned long)diagSkipped);
  queueDiag(type, micros(), stepper.currentPosition(), counts);
}

void serviceDiagnostics() {
  if (diagEnabled && (uint32_t)(millis() - diagLastStatus) >= 1000) {
    diagLastStatus = millis();
    queueDiagStatus('H');
  }
  if (!diagEnabled && !diagCount && !diagTxLength) return;
  if (!diagTxLength && diagCount) {
    const DiagEvent &event = diagEvents[diagHead];
    const char shortType[2] = {event.type, 0};
    const char *type = event.type == 'O' ? "ON" : event.type == 'F' ? "OFF" : shortType;
    diagTxLength = snprintf(diagTx, sizeof(diagTx), "DBG,%s,%lu,%lu,%ld,%s\n", type,
                            (unsigned long)event.at, (unsigned long)event.sequence,
                            (long)event.position, event.detail);
    diagHead = (diagHead + 1) % DIAG_CAPACITY;
    --diagCount;
  }
  const int available = Serial.availableForWrite();
  // 한 줄을 한 번에 보내 DONE/STOPPED 같은 일반 응답이 진단 줄 중간에 끼지 않게 한다.
  if (diagTxLength && available >= (int)diagTxLength) {
    Serial.write((const uint8_t *)diagTx, diagTxLength);
    diagTxLength = 0;
  }
}

enum Mode { STOPPED, AUTO_MOVE, JOG_MOVE };
Mode currentMode = STOPPED;
String inputBuffer = "";

void driverEnable(bool on) {
  digitalWrite(ENABLE_PIN, on ? LOW : HIGH);
}

void setup() {
  Serial.begin(115200);
  pinMode(ENABLE_PIN, OUTPUT);

  stepper.setMinPulseWidth(20);         // A4988 인식용 펄스 폭 확보
  stepper.setMaxSpeed(MAX_SPEED_SPS);
  stepper.setAcceleration(ACCEL_SPS2);

  driverEnable(true);
  Serial.println("READY");
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      inputBuffer.trim();
      processCommand(inputBuffer);
      inputBuffer = "";
    } else if (c != '\r') {
      inputBuffer += c;
    }
  }

  if (currentMode != STOPPED) {
    stepper.run();
    if (currentMode == AUTO_MOVE && stepper.distanceToGo() == 0) {
      currentMode = STOPPED;
      Serial.println("DONE");
    }
  }
  serviceDiagnostics();
}

float clampSpeed(float mmPerSec) {
  float sps = mmPerSec * STEPS_PER_MM;
  if (sps > MAX_SPEED_SPS) sps = MAX_SPEED_SPS;
  if (sps < 1.0) sps = 1.0;
  return sps;
}

void processCommand(const String &cmd) {
  if (cmd == "DIAG,ON") {
    diagHead = diagCount = 0;
    diagTxLength = 0;
    diagSequence = diagDropped = diagSkipped = 0;
    diagLastStatus = millis();
    diagEnabled = true;
    queueDiag('O', micros(), stepper.currentPosition(), "1");
    return;
  }
  if (cmd == "DIAG,OFF") {
    if (diagEnabled) {
      diagEnabled = false;
      queueDiagStatus('F');
    }
    return;
  }
  if (diagEnabled) queueDiag('C', micros(), stepper.currentPosition(), cmd.c_str());
  if (cmd == "STOP") {
    stepper.stop();
    while (stepper.distanceToGo() != 0) stepper.run();
    currentMode = STOPPED;
    Serial.println("STOPPED");
  }
  else if (cmd == "MOFF") {
    haltNow();
    driverEnable(false);
    Serial.println("MOTOR_OFF");
  }
  else if (cmd == "MON") {
    driverEnable(true);
    Serial.println("MOTOR_ON");
  }
  else if (cmd.startsWith("start,")) {
    int c1 = cmd.indexOf(',');
    int c2 = cmd.indexOf(',', c1 + 1);
    if (c1 > 0 && c2 > 0) {
      float dist  = cmd.substring(c1 + 1, c2).toFloat();
      float speed = cmd.substring(c2 + 1).toFloat();
      if (speed > 0 && dist != 0) {
        driverEnable(true);
        stepper.setMaxSpeed(clampSpeed(speed));
        stepper.move((long)(dist * STEPS_PER_MM));
        currentMode = AUTO_MOVE;
      }
    }
  }
  else if (cmd.startsWith("JU,")) {
    driverEnable(true);
    stepper.setMaxSpeed(clampSpeed(cmd.substring(3).toFloat()));
    stepper.move(1000000L);
    currentMode = JOG_MOVE;
  }
  else if (cmd.startsWith("JD,")) {
    driverEnable(true);
    stepper.setMaxSpeed(clampSpeed(cmd.substring(3).toFloat()));
    stepper.move(-1000000L);
    currentMode = JOG_MOVE;
  }
}

void haltNow() {
  stepper.setCurrentPosition(stepper.currentPosition());
  currentMode = STOPPED;
}
