// =====================================================================
//  1층 바이오룸 수평 압축 장비 펌웨어 (v3)
//  Arduino Mega 2560 + RAMPS 1.4 (Y축 소켓) + A4988 + 14HR08-0654S
//
//  직렬 115200 bps, 줄 끝 '\n'
//    start,<mm>,<mm/s>   자동 이동 (양수 = 전진 = 시편을 누르는 방향)  → 완료 시 "DONE"
//    JF,<mm/s>           조그 전진 (누르고 있는 동안)
//    JB,<mm/s>           조그 후진
//    STOP                감속 정지. 드라이버는 여자 상태 유지(위치 고정)   → "STOPPED"
//    MOFF                즉시 정지 + 드라이버 전원 차단 (축 자유)          → "MOTOR_OFF"
//    MON                 드라이버 전원 인가                               → "MOTOR_ON"
//  하드웨어 비상정지 버튼 (D3, Active LOW, 내부 풀업) → 정지 후 "STOP_BUTTON"
// =====================================================================
#include <AccelStepper.h>

#define STEP_PIN    60   // RAMPS Y-STEP
#define DIR_PIN     61   // RAMPS Y-DIR
#define ENABLE_PIN  56   // RAMPS Y-ENABLE (LOW = 드라이버 활성)
#define STOP_PIN    3    // 비상정지 버튼 (사용하지 않으면 미배선 상태로 두면 됨: 풀업이라 HIGH 유지)

const float STEPS_PER_MM   = 800.0;   // 실측 확인됨
const float MAX_SPEED_SPS  = 5000.0;  // steps/s 상한
const float ACCEL_SPS2     = 2000.0;  // steps/s^2

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
  pinMode(STOP_PIN, INPUT_PULLUP);

  stepper.setMinPulseWidth(20);
  stepper.setMaxSpeed(MAX_SPEED_SPS);
  stepper.setAcceleration(ACCEL_SPS2);

  driverEnable(true);      // 부팅 직후부터 위치 유지
  Serial.println("READY");
}

void loop() {
  // 하드웨어 비상정지
  if (currentMode != STOPPED && digitalRead(STOP_PIN) == LOW) {
    haltNow();
    Serial.println("STOP_BUTTON");
  }

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
    stepper.stop();                       // 감속 정지 (저속에서는 사실상 즉시)
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
  else if (cmd.startsWith("JF,")) {
    driverEnable(true);
    stepper.setMaxSpeed(clampSpeed(cmd.substring(3).toFloat()));
    stepper.move(1000000L);
    currentMode = JOG_MOVE;
  }
  else if (cmd.startsWith("JB,")) {
    driverEnable(true);
    stepper.setMaxSpeed(clampSpeed(cmd.substring(3).toFloat()));
    stepper.move(-1000000L);
    currentMode = JOG_MOVE;
  }
}

// 감속 없이 즉시 멈춤 (비상정지·전원 차단용)
void haltNow() {
  stepper.setCurrentPosition(stepper.currentPosition());  // 목표 = 현재 → 즉시 정지, 속도 0
  currentMode = STOPPED;
}
