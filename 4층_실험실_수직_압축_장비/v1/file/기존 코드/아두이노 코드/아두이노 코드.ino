#include <AccelStepper.h>

// RAMPs 1.4 핀 번호
#define stepPin     60
#define dirPin      61
#define enablePin   56
#define stopPin     3

const float stepsPerMM = 800.0;  // 예: 1.8도 모터, 1mm 피치, 16 microsteps
AccelStepper stepper(AccelStepper::DRIVER, stepPin, dirPin);

float targetDistance = 0.0;
float targetSpeed = 0.0;
bool isRunning = false;
String inputBuffer = "";

void setup() {
  Serial.begin(9600); 
  pinMode(enablePin, OUTPUT);
  pinMode(stopPin, INPUT_PULLUP);

  digitalWrite(enablePin, LOW); // 모터 드라이버 활성화

  stepper.setAcceleration(1000); // 가속도 설정 (steps/s^2)
  stepper.setMaxSpeed(5000);     // 최대속도 제한 (steps/s)

  Serial.println("READY");
}

void loop() {
  // STOP 버튼으로 중지
  if (digitalRead(stopPin) == LOW && isRunning) {
    stopMotor();
    Serial.println("STOP_BUTTON");
  }

  // 비동기 시리얼 명령 수신
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      inputBuffer.trim();
      if (inputBuffer.startsWith("start")) {
        parseCommand(inputBuffer);
        startMotor();  // 여전히 상대 이동
      } else if (inputBuffer == "STOP") {
        stopMotor();
        Serial.println("STOPPED");
      }
      inputBuffer = "";
    } else {
      inputBuffer += c;
    }
  }

  // 모터 실행
  if (isRunning) {
    stepper.run();
    if (!stepper.isRunning()) {
      stopMotor();
      Serial.println("DONE");
    }
  }
}

// 명령 파싱: start,거리(mm),속도(mm/s)
void parseCommand(String cmd) {
  int idx1 = cmd.indexOf(',');
  int idx2 = cmd.indexOf(',', idx1 + 1);
  targetDistance = cmd.substring(idx1 + 1, idx2).toFloat();
  targetSpeed = cmd.substring(idx2 + 1).toFloat();
}

// 모터 시작
void startMotor() {
  if (targetSpeed <= 0 || targetDistance == 0) return;  // 속도는 양수, 거리는 음수도 허용

  long stepsToMove = targetDistance * stepsPerMM;
  stepper.setMaxSpeed(targetSpeed * stepsPerMM);
  stepper.move(stepsToMove);  // ✅ 상대 이동으로 변경
  digitalWrite(enablePin, LOW);
  isRunning = true;

  Serial.print("STARTED,");
  Serial.println(millis());
}

// 모터 정지
void stopMotor() {
  stepper.stop();
  stepper.setCurrentPosition(0); // 원점으로 리셋
  digitalWrite(enablePin, HIGH);
  isRunning = false;
}
