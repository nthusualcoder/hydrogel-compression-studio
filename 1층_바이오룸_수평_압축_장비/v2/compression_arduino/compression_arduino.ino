#include <AccelStepper.h>

// RAMPS 1.4 Y축 핀 번호
#define stepPin     60
#define dirPin      61
#define enablePin   56
#define stopPin     3

const float stepsPerMM = 800.0;
AccelStepper stepper(AccelStepper::DRIVER, stepPin, dirPin);

float targetDistance = 0.0;
float targetSpeed = 0.0;

enum Mode { STOPPED, AUTO_MOVE, JOG_MOVE };
Mode currentMode = STOPPED;
String inputBuffer = "";

void setup() {
  Serial.begin(115200); 
  
  pinMode(enablePin, OUTPUT);
  pinMode(stopPin, INPUT_PULLUP);

  // 모터에 전기 공급 중단(초기 발열 방지 및 안전 목적)
  // 작동 시작 명령이 들어오면 LOW로 인가하여 구동합니다.
  digitalWrite(enablePin, HIGH); 

  stepper.setAcceleration(2000); 
  stepper.setMaxSpeed(5000);     

  Serial.println("READY");
}

void loop() {
  // 비상 정지 버튼 트리거 (Active LOW)
  if (digitalRead(stopPin) == LOW && currentMode != STOPPED) {
    stopMotor();
    Serial.println("STOP_BUTTON");
  }

  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      inputBuffer.trim();
      
      if (inputBuffer.startsWith("start")) {
        parseCommand(inputBuffer);
        startMotor();
      } 
      else if (inputBuffer.startsWith("JF,")) { // 조그 전진
        float speed = inputBuffer.substring(3).toFloat();
        stepper.setMaxSpeed(speed * stepsPerMM);
        stepper.move(1000000); 
        currentMode = JOG_MOVE;
        digitalWrite(enablePin, LOW); // 모터 활성화
      } 
      else if (inputBuffer.startsWith("JB,")) { // 조그 후진
        float speed = inputBuffer.substring(3).toFloat();
        stepper.setMaxSpeed(speed * stepsPerMM);
        stepper.move(-1000000); 
        currentMode = JOG_MOVE;
        digitalWrite(enablePin, LOW); // 모터 활성화
      } 
      else if (inputBuffer == "STOP") {
        stopMotor();
        Serial.println("STOPPED");
      }
      inputBuffer = "";
    } else {
      inputBuffer += c;
    }
  }

  if (currentMode != STOPPED) {
    stepper.run();
    if (currentMode == AUTO_MOVE && !stepper.isRunning()) {
      stopMotor();
      Serial.println("DONE");
    }
  }
}

void parseCommand(String cmd) {
  int idx1 = cmd.indexOf(',');
  int idx2 = cmd.indexOf(',', idx1 + 1);
  targetDistance = cmd.substring(idx1 + 1, idx2).toFloat();
  targetSpeed = cmd.substring(idx2 + 1).toFloat();
}

void startMotor() {
  if (targetSpeed <= 0 || targetDistance == 0) return;
  long stepsToMove = targetDistance * stepsPerMM;
  stepper.setMaxSpeed(targetSpeed * stepsPerMM);
  stepper.move(stepsToMove); 
  digitalWrite(enablePin, LOW); // 모터 활성화
  currentMode = AUTO_MOVE;
}

void stopMotor() {
  stepper.stop();
  stepper.setCurrentPosition(0); 
  digitalWrite(enablePin, HIGH); // 대기 중 모터 비활성화(발열 방지)
  currentMode = STOPPED;
}
