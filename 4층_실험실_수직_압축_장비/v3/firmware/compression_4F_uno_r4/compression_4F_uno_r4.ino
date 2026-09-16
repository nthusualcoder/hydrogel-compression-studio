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

#define DIR_PIN     2
#define STEP_PIN    3
#define ENABLE_PIN  4    // A4988 EN (LOW = 활성). 배선하지 않으면 무해.

const float STEPS_PER_MM   = 800.0;   // 실측 확인됨
const float MAX_SPEED_SPS  = 4000.0;
const float ACCEL_SPS2     = 2000.0;

AccelStepper stepper(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);

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
}

float clampSpeed(float mmPerSec) {
  float sps = mmPerSec * STEPS_PER_MM;
  if (sps > MAX_SPEED_SPS) sps = MAX_SPEED_SPS;
  if (sps < 1.0) sps = 1.0;
  return sps;
}

void processCommand(const String &cmd) {
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
