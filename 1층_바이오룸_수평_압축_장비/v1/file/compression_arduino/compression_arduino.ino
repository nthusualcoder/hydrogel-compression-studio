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
  // ⭐ 파이썬과 동일하게 115200으로 통신 속도 일치!
  Serial.begin(115200); 
  
  pinMode(enablePin, OUTPUT);
  pinMode(stopPin, INPUT_PULLUP);

  // 모터에 전기 공급 (이 코드가 실행되어야 '징~' 소리가 납니다)
  digitalWrite(enablePin, LOW); 

  stepper.setAcceleration(2000); 
  stepper.setMaxSpeed(5000);     

  Serial.println("READY");
}

void loop() {
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
      else if (inputBuffer.startsWith("JF,")) { // 파이썬의 전진 명령
        float speed = inputBuffer.substring(3).toFloat();
        stepper.setMaxSpeed(speed * stepsPerMM);
        stepper.move(1000000); 
        currentMode = JOG_MOVE;
        digitalWrite(enablePin, LOW);
      } 
      else if (inputBuffer.startsWith("JB,")) { // 파이썬의 후진 명령
        float speed = inputBuffer.substring(3).toFloat();
        stepper.setMaxSpeed(speed * stepsPerMM);
        stepper.move(-1000000); 
        currentMode = JOG_MOVE;
        digitalWrite(enablePin, LOW);
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
  digitalWrite(enablePin, LOW);
  currentMode = AUTO_MOVE;
}

void stopMotor() {
  stepper.stop();
  stepper.setCurrentPosition(0); 
  // 대기 중 발열을 줄이려면 HIGH, 계속 힘을 주어 버티게 하려면 LOW로 유지하세요.
  digitalWrite(enablePin, HIGH); 
  currentMode = STOPPED;
}