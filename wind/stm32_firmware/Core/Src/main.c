/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "wind_control.h"
#include <string.h>
#include <stdio.h>
#include <math.h>
#include "cJSON.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
/* ESP8266 硬复位引脚（PA4） - 拉低 RST > 10ms 模块必然回到 AT 命令模式 */
#define ESP_RST_PORT    GPIOA
#define ESP_RST_PIN     GPIO_PIN_4
#define ESP_RST_LOW()   HAL_GPIO_WritePin(ESP_RST_PORT, ESP_RST_PIN, GPIO_PIN_RESET)
#define ESP_RST_HIGH()  HAL_GPIO_WritePin(ESP_RST_PORT, ESP_RST_PIN, GPIO_PIN_SET)
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
 UART_HandleTypeDef huart1;
UART_HandleTypeDef huart2;

/* USER CODE BEGIN PV */
// NOTE: per interface.md v0.3 the C side never sends PUSH (PUSH is A->C only).
// Telemetry/pitch feedback to A goes through YT2, so the old point_map /
// tx_buffer PUSH builder has been removed.
// 全局风机参数
WindParams_t g_wind_params;
uint8_t g_control_mode = 1;  // 控制模式：0 = 开环，1 = 闭环

/* ---- UART RX buffers (one set per port, no cross-talk) ----
 * USART2: local debug/config link (short lines) -> 256 B is enough.
 * USART1: transparent link to A. interface.md 4.1 allows frames up to 4096 B;
 * A's PUSH frames carry 13-digit unix-ms timestamps and run ~240-260 B, so a
 * 256 B buffer wraps them and cJSON fails -> that was the "A PUSH parse ERROR".
 * 512 B covers the real frame sizes with margin. */
#define RX_BUF_SIZE 256
uint8_t rx2_buffer[RX_BUF_SIZE];
uint16_t rx2_index = 0;
uint8_t rx2_byte;
/* Completed-line snapshot + flag: the ISR must NEVER call ParseConfigCommand
 * directly. cJSON_Parse calls malloc, and newlib's malloc is NOT reentrant on
 * bare metal - an allocation started in the main loop (e.g. printing a HEART
 * frame) would be interleaved by an allocation in the ISR, corrupting the
 * heap freelist. That corruption prints heap garbage (the "non UTF-8 frame"
 * A complained about) and soon after hardfaults into silent while(1).
 * Same snapshot pattern as rx1 below: parse in the MAIN loop only. */
uint8_t rx2_line[RX_BUF_SIZE];
volatile uint8_t rx2_line_ready = 0;

#define RX1_BUF_SIZE 512
uint8_t rx1_buffer[RX1_BUF_SIZE];       /* ISR line accumulation buffer       */
uint16_t rx1_index = 0;
uint8_t rx1_byte;
uint8_t rx1_line[RX1_BUF_SIZE];         /* completed-line snapshot for main   */
volatile uint8_t  rx1_line_ready = 0;   /* 1 = rx1_line holds one full frame  */
volatile uint32_t rx1_lines_dropped = 0;/* debug: lines lost while busy       */

int sim_time = 0;
/* Last HAL_GetTick sample used to advance sim_time on wallclock. Decoupling
 * sim_time from the main-loop tick counter fixes the "sim_time jumps several
 * seconds when WiFi is down" symptom: a slow LinkReconnect cycle (multi-
 * second) used to keep sim_time at +1 while wallclock advanced 5-10 s. The
 * anchor is updated to "now - remainder" so fractional seconds are not lost
 * across cycles. Resets cleanly on warm boot (HAL_GetTick returns 0). */
static uint32_t s_sim_anchor_ms = 0;
// 风机运行状态（主循环里使用）
WindState_t wind_state;

/* A 推来的实际值：所有 YC 都是 A→C（A 收到 C 的 YT 回传后按仿真模型再算出
 * YC2/YC3/YC4 推给 C，做一致性比对）。C 不向 A 发 YC，YC 仅供本地 GUI 镜像。
 * 链路在线且收到过该点就显示 A 的实测值，否则退回本地计算值。 */
static float    g_a_output   = 0.0f;   /* A 推来的实际有功出力 (YC2) */
static float    g_a_pitch    = 0.0f;   /* A 推来的实际桨距角   (YC3) */
static float    g_a_avail    = 0.0f;   /* A 推来的实际可用功率 (YC4) */
static uint32_t g_a_output_ms = 0;     /* 最后收到 YC2 的 tick       */
static uint32_t g_a_pitch_ms  = 0;     /* 最后收到 YC3 的 tick       */
static uint32_t g_a_avail_ms  = 0;     /* 最后收到 YC4 的 tick       */

/* tick of the last telemetry snapshot sent to the GUI. Used by LinkWaitYield so
 * the panel keeps showing fresh data (wind/power/last-update) even while the
 * WiFi link is being re-established, instead of freezing and looking "断连". */
uint32_t g_last_gui_ms = 0;

/* ---- link supervision (detect A kicking us off, then re-connect + re-REG) ----
 * In transparent mode the ESP8266 still prints status words on the serial link:
 *   "CLOSED"         -> the TCP peer (A) closed the socket, e.g. A's REG
 *                       handler kicks the previous connection of the same role
 *   "WIFI DISCONNECT" -> the AP link was lost
 * Both are flagged by the RX ISR below. A normally pushes every ~1 s, so
 * LINK_SILENCE_MS with no inbound byte at all also means dead link even when
 * the module reported nothing (half-open TCP). */
volatile uint8_t  g_link_up = 0;       /* 0 = transparent session is down    */
volatile uint8_t  g_link_closed = 0;   /* saw "CLOSED" from ESP8266          */
volatile uint8_t  g_wifi_lost = 0;     /* saw "WIFI DISCONNECT" from ESP8266 */
volatile uint32_t g_last_rx1_ms = 0;   /* tick of last inbound byte on USART1*/
#define LINK_SILENCE_MS 20000          /* A silent this long = link is dead  */

/* ---- clock sync (interface.md 4.2 + rule 3) --------------------------
 * ts in every frame MUST be Unix milliseconds, and A is the ONLY clock
 * source. The MCU has no RTC, so we keep an offset:
 *     unix_ms = HAL_GetTick() + g_ts_offset
 * and re-sync it from the ts of every inbound frame (ACK/NACK/HEART/PUSH).
 * At cold boot the offset is 0, so the very first REG may carry a wrong
 * ts; A answers NACK - whose ts we sync from - and the immediate retry
 * inside DoRegister registers with valid time. */
volatile int64_t g_ts_offset = 0;

/* ---- pending command ACK bookkeeping (interface.md 5.5) ----
 * Every YK/YT we send is registered here; the matching ACK/NACK from A
 * clears the slot. A slot older than 2 s is resent once, then flagged. */
typedef struct {
    uint8_t  used;      /* slot in use                        */
    char     code[3];   /* "YK" / "YT"                        */
    char     type[3];   /* "YK" / "YT"                        */
    int      pt;
    double   val;       /* kept for the one allowed resend    */
    uint32_t sent_ms;
    uint8_t  retries;   /* 0 = first send, 1 = resent once    */
} AckPending_t;
AckPending_t g_ack_pending[4];

// WiFi 配置（请改成你实测能连上的路由器）
#define WIFI_SSID "yxtwifi"
#define WIFI_PASS "vqcx3322"

/* A 端服务器地址：编译默认值 + 运行时可由上位机 set_server 命令修改
 * （评分表 34 项"能在界面设置通信IP地址"）。重启后回默认值，
 * GUI 每次串口连接成功会自动把数据库里保存的地址重新推过来。 */
#define TCP_SERVER_IP_DEFAULT  "10.18.134.231"
#define TCP_SERVER_PORT_DEFAULT 9000
char    g_server_ip[24] = TCP_SERVER_IP_DEFAULT;
uint16_t g_server_port  = TCP_SERVER_PORT_DEFAULT;

// 串口收发临时缓冲区（AT 命令收发）
char wifi_rx_buf[256];
char wifi_tx_buf[256];

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART1_UART_Init(void);
static void MX_USART2_UART_Init(void);
/* USER CODE BEGIN PFP */
// 在 main() 之前声明，用于文件作用域。若放到 main() 体内会报 "storage class invalid"；
// 若不提前声明，main() 第 162 行调用 WiFi_Init() 会被隐式声明成 int WiFi_Init()，
// 与后面 static uint8_t WiFi_Init(void) 的定义类型冲突。
static uint8_t WiFi_Init(void);
void SendControlToA(WindState_t *state);
static void SendTelemetryToGUI(WindState_t *state);
void ParseNetMessage(char *json_str);
void SendHeartbeat(void);
void CheckAckTimeout(void);
static uint8_t LinkReconnect(void);
static uint64_t NowUnixMs(void);
static void SyncTimeFromA(double a_ts_ms);
static uint8_t WiFi_WaitIpdLine(char *out, uint16_t out_sz, uint32_t timeout_ms);
static uint8_t FrameLooksJson(const char *s, uint32_t len);
void ParseConfigCommand(char *json_str);
void FaultReport(void);
static void FaultPrint(const char *s);
static void ServiceConfigNow(void);
static void LinkWaitYield(uint32_t ms);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{
  /* USER CODE BEGIN 1 */
  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USART1_UART_Init();
  MX_USART2_UART_Init();
  /* USER CODE BEGIN 2 */
  // 初始化默认参数
  g_wind_params.cut_in = 3.0f;
  g_wind_params.rated_wind = 12.0f;
  g_wind_params.cut_out = 25.0f;
  g_wind_params.rated_power = 100.0f;
  g_wind_params.max_pitch = 30.0f;
  g_control_mode = 1;  // 默认闭环
  WindControl_Init(&wind_state, &g_wind_params);   // 传入地址，建立绑定

  // 启动 USART2 接收中断（接收上位机配置命令）
  HAL_UART_Receive_IT(&huart2, &rx2_byte, 1);
  // USART1 接收中断等透传建立后再开，避免启动期乱码污染解析
  HAL_UART_Transmit(&huart2, (uint8_t*)"STM32 Ready\r\n", 13, 100);

  // ESP8266 + WiFi 全流程初始化（PA4 硬复位 + AT/CWMODE/CWJAP/CIPSTART/REG/透传）
  if (WiFi_Init() != 0) {
      // 失败：不再死循环等人工复位。置 g_link_up=0，主循环 3b 的链路监督
      // （LinkReconnect）会每秒后台重试整个流程（含硬复位与重注册），
      // 期间 GUI 遥测 / 参数下发（USART2）全部照常工作（风机按最后一次收到的
      // 风速/设定值运行）；
      // 服务器恢复（例如 A 同学把服务端开起来 / IP 修好后重新烧录）即自动连上。
      g_link_up = 0;
      g_last_rx1_ms = HAL_GetTick();
      HAL_UART_Transmit(&huart2, (uint8_t*)"INIT FAIL, keep retrying...\r\n", 29, 200);
  }
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
	  /* 仿真时刻按 wallclock 推进，与主循环节奏解耦：
	   *   - 主循环被 LinkReconnect 阻塞 N 秒时，下一圈 sim_time += N/1000（补回真实秒数）
	   *   - A 在线时本变量仍会被 HEART 帧按"A 为权威时基"覆盖（rule 3，本地逻辑不变） */
	  {
		  uint32_t now_ms = HAL_GetTick();
		  if (s_sim_anchor_ms == 0) {
			  s_sim_anchor_ms = now_ms;
		  } else {
			  uint32_t dt_ms = now_ms - s_sim_anchor_ms;
			  if (dt_ms >= 1000) {
				  sim_time += (int)(dt_ms / 1000);
				  s_sim_anchor_ms = now_ms - (dt_ms % 1000);
			  }
		  }
	  }

     /* 1. 风速 / 功率设定：唯一来源是 A 的 PUSH（YC1 风速 / YT1 功率设定）。
      *    开环、闭环都只从 A 收，本地不再做任何模拟。
      *    原先"5 s 未收到 PUSH 就用本地正弦"是个调试兜底，A 的 PUSH 周期只要
      *    大于 5 s 就会被周期性触发，把风速和功率设定覆盖成正弦值 —— 风速、
      *    功率、桨距角三条曲线于是每隔几秒一起乱蹦。已删除。
      *    A 未上线 / 断链时 wind_state 保持上一次的值（上电初值 0）：
      *    风速 0 < 切入风速 → run=0，风机停机，不会往 GUI 发垃圾数据。 */

     // 2. 调用控制算法
     WindControl_Update(&wind_state);

     /* 2b. local monitor link ONLY (USART2 -> PC GUI): full telemetry snapshot.
      * Never goes to huart1/WiFi - interface.md 4.3 lets C send only
      * REG/HEART/YK/YT to A, so the GUI data feed lives on this serial link. */
     SendTelemetryToGUI(&wind_state);

     /* 3. parse one completed inbound frame (moved OUT of the RX interrupt:
           cJSON inside the ISR blocked the re-arm and dropped bytes) */
     if (rx1_line_ready) {
         ParseNetMessage((char*)rx1_line);
         rx1_line_ready = 0;
     }

     /* 3c. same treatment for the GUI config link (USART2): the ISR used to
           call ParseConfigCommand (-> cJSON -> malloc) directly, which raced
           the main-loop malloc and corrupted the heap. ServiceConfigNow clears
           the "ready" flag BEFORE parsing, so a frame completing mid-parse is
           not dropped. */
     ServiceConfigNow();

     /* 3b. link supervision: the ESP8266 reported CLOSED / WIFI DISCONNECT,
           or A has been completely silent for 20 s -> tear the transparent
           session down and re-establish it INCLUDING a fresh REG. This is
           required: A's REG handler kicks the old socket, so a new TCP
           connection must register again before any HEART/YK/YT is accepted.
           If LinkReconnect fails it returns with g_link_up == 0 and the next
           cycle (1 s later) tries again. */
     if (!g_link_up || g_link_closed || g_wifi_lost ||
         (HAL_GetTick() - g_last_rx1_ms > LINK_SILENCE_MS)) {
         LinkReconnect();
     }

     /* 4~6. traffic to A only while the transparent session is up: writing
            into a half-open link (or an AT-mode module) just produces
            garbage. Local sim + GUI telemetry above keep running either way. */
     if (g_link_up) {
         /* 4. YK/YT whose ACK is late: resend once, then flag error (5.5) */
         CheckAckTimeout();

         /* 5. heartbeat every cycle ~1 s (interface.md 5.2 requires HEART,
               otherwise A declares C offline after 15 s in open-loop mode) */
         SendHeartbeat();

         // 6. 闭环：回传启停状态 + 桨距角设定值（YK1 / YT2）
         if (g_control_mode == 1) {
             SendControlToA(&wind_state);
         }
     }

     HAL_Delay(1000);
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1_BOOST);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = RCC_PLLM_DIV6;
  RCC_OscInitStruct.PLL.PLLN = 85;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = RCC_PLLQ_DIV2;
  RCC_OscInitStruct.PLL.PLLR = RCC_PLLR_DIV2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_4) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief USART1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART1_UART_Init(void)
{

  /* USER CODE BEGIN USART1_Init 0 */

  /* USER CODE END USART1_Init 0 */

  /* USER CODE BEGIN USART1_Init 1 */

  /* USER CODE END USART1_Init 1 */
  huart1.Instance = USART1;
  huart1.Init.BaudRate = 115200;
  huart1.Init.WordLength = UART_WORDLENGTH_8B;
  huart1.Init.StopBits = UART_STOPBITS_1;
  huart1.Init.Parity = UART_PARITY_NONE;
  huart1.Init.Mode = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  huart1.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
  huart1.Init.ClockPrescaler = UART_PRESCALER_DIV1;
  huart1.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
  if (HAL_UART_Init(&huart1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetTxFifoThreshold(&huart1, UART_TXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetRxFifoThreshold(&huart1, UART_RXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_DisableFifoMode(&huart1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART1_Init 2 */

  /* USER CODE END USART1_Init 2 */

}

/**
  * @brief USART2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART2_UART_Init(void)
{

  /* USER CODE BEGIN USART2_Init 0 */

  /* USER CODE END USART2_Init 0 */

  /* USER CODE BEGIN USART2_Init 1 */

  /* USER CODE END USART2_Init 1 */
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 115200;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  huart2.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
  huart2.Init.ClockPrescaler = UART_PRESCALER_DIV1;
  huart2.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
  if (HAL_UART_Init(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetTxFifoThreshold(&huart2, UART_TXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetRxFifoThreshold(&huart2, UART_RXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_DisableFifoMode(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART2_Init 2 */

  /* USER CODE END USART2_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOF_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOA, GPIO_PIN_4, GPIO_PIN_RESET);

  /*Configure GPIO pin : PA4 */
  GPIO_InitStruct.Pin = GPIO_PIN_4;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

}

/* USER CODE BEGIN 4 */
// File-scope 原型声明：让上面的 WiFi_Init 能看到下面定义的 helper。
// 不要再把声明放到 main() 函数体内，会报"storage class invalid"。
uint8_t WiFi_SendCmd(char *cmd, char *ack, uint32_t timeout_ms);
uint8_t DoRegister(void);
void ParseConfigCommand(char *json_str);
static void DebugPrint(const char *s);
static void SendCmdFrame(const char *code, const char *type, int pt, double val);
static void AckPendingPut(const char *code, const char *type, int pt, double val);
static uint8_t AckPendingClear(const char *type, int pt);

// =====================================================================
// ESP8266 链路：硬件复位 + WiFi/TCP/透传 全流程
// 原理：无论模块上次是 AT 命令模式还是残留的透传模式，
//       拉低 RST > 10ms 后释放，模块必然重启进入 AT 命令模式。
//       这是从根本解决"时好时坏"问题的关键。
// =====================================================================

// 初始化 PA4 为推挽输出，默认高电平（不复位）
static void ESP_GPIO_Init(void) {
    GPIO_InitTypeDef gpio = {0};
    __HAL_RCC_GPIOA_CLK_ENABLE();   // MX_GPIO_Init 已开过，这里再开一次保底
    gpio.Pin   = ESP_RST_PIN;
    gpio.Mode  = GPIO_MODE_OUTPUT_PP;
    gpio.Pull  = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(ESP_RST_PORT, &gpio);
    ESP_RST_HIGH();
}

// 拉低 RST ~50ms 再拉高，等同于一次硬复位
static void ESP_HardReset(void) {
    ESP_RST_LOW();
    HAL_Delay(50);
    ESP_RST_HIGH();
}

// 清空 USART1 接收侧：先清 ORE/FE/NE 错误标志，再逐字节读走残留数据。
// 模块重启横幅（74880 波特的乱码）或上一条命令的迟到回复都会残留在
// 硬件寄存器里，不清掉会污染后续 strstr 匹配。
static void UART1_Flush(void) {
    uint8_t b;
    __HAL_UART_CLEAR_FLAG(&huart1, UART_CLEAR_OREF | UART_CLEAR_FEF | UART_CLEAR_NEF);
    while (HAL_UART_Receive(&huart1, &b, 1, 5) == HAL_OK) {}
}

/* 全流程初始化：硬复位 → AT → CWMODE → CWJAP → CIPSTART → DoRegister → 透传。
 * 返回：0 = 成功进入透传；非 0 = 失败（错误码已打到 USART2） */
static uint8_t WiFi_Init(void) {
    HAL_UART_Transmit(&huart2, (uint8_t*)"WiFi Init...\r\n", 14, 100);

    // 0) 硬件复位 ESP8266（前提：PA4 已接到模块 RST 脚）
    ESP_GPIO_Init();
    HAL_Delay(300);          // 等模块上电稳定
    ESP_HardReset();
    HAL_Delay(2000);         // 等模块重启完成

    // 0b) 软件兜底：如果 RST 线没接/无效、模块残留在透传模式，
    //     发 +++ 使其退出透传（AT 固件要求前后各 >1s 静默），再清掉接收残留。
    //     若模块本来就在 AT 模式，+++ 是非法命令，只会收到 ERROR，无副作用。
    UART1_Flush();
    HAL_UART_Transmit(&huart1, (uint8_t*)"+++", 3, 100);
    HAL_Delay(1200);
    UART1_Flush();

    // 1) 测试 AT：带重试（最多 5 次），偶发失败不再一击致命
    uint8_t at_ok = 0;
    for (int i = 0; i < 5 && !at_ok; i++) {
        if (WiFi_SendCmd("AT", "OK", 2000)) {
            at_ok = 1;
        } else {
            HAL_Delay(500);
            UART1_Flush();
        }
    }
    if (!at_ok) {
        // 把模块最后一次的实际回复打出来，区分三种情况：
        //   空/乱码   → 透传残留或模块没起来（查 RST 接线/供电）
        //   ERROR     → 模块在命令模式但未就绪（重试已覆盖，查供电）
        //   旧回复    → 上一条命令残留（UART1_Flush 已覆盖）
        HAL_UART_Transmit(&huart2, (uint8_t*)"AT FAIL (1), rsp: ", 18, 100);
        HAL_UART_Transmit(&huart2, (uint8_t*)wifi_rx_buf, strlen(wifi_rx_buf), 100);
        HAL_UART_Transmit(&huart2, (uint8_t*)"\r\n", 2, 100);
        return 1;
    }
    HAL_UART_Transmit(&huart2, (uint8_t*)"AT OK\r\n", 7, 100);

    // 2) Station 模式
    if (!WiFi_SendCmd("AT+CWMODE=1", "OK", 2000)) {
        HAL_UART_Transmit(&huart2, (uint8_t*)"CWMODE FAIL (2)\r\n", 18, 100);
        return 2;
    }

    // 2b) 强制回到非透传配置。上次运行结束时模块被设成 CIPMODE=1，
    //     若模块没有真正断电复位（PA4 未接 RST 时就是这种情况），该配置会残留：
    //     +++ 只能退出透传会话，改不了 CIPMODE。残留时 AT+CIPSEND=<长度>
    //     直接回 ERROR，而 REG 阶段用的正是带长度的 CIPSEND。
    WiFi_SendCmd("AT+CIPMODE=0", "OK", 2000);

    // 3) 连接 WiFi
    sprintf(wifi_tx_buf, "AT+CWJAP=\"%s\",\"%s\"", WIFI_SSID, WIFI_PASS);
    if (!WiFi_SendCmd(wifi_tx_buf, "GOT IP", 15000)) {
        HAL_UART_Transmit(&huart2, (uint8_t*)"WiFi FAIL (3)\r\n", 16, 100);
        return 3;
    }
    HAL_UART_Transmit(&huart2, (uint8_t*)"WiFi OK\r\n", 10, 100);

    // 4) 连接 TCP Server（重试 3 次；失败打印模块原始回复定位原因：
    //    回 ERROR = 对方端口没监听/被防火墙拦；完全无回复 = IP 不通/不在
    //    同一网段（最常见：对方电脑 DHCP 换了 IP，需更新 TCP_SERVER_IP））
    {
        char tip[64];
        sprintf(tip, "TCP -> %s:%d\r\n", g_server_ip, g_server_port);
        DebugPrint(tip);
    }
    uint8_t tcp_ok = 0;
    for (int i = 0; i < 3 && !tcp_ok; i++) {
        // 4a) 先关掉上次运行残留的旧 TCP 连接（模块没断电时旧连接还在；
        //     若旧连接已被服务器单方面断开而模块未察觉，此时 CIPSTART 会回
        //     ALREADY CONNECTED，后续 CIPSEND 必然 ERROR）
        WiFi_SendCmd("AT+CIPCLOSE", "CLOSED", 1000);   // 无连接时回 ERROR，忽略即可
        UART1_Flush();

        sprintf(wifi_tx_buf, "AT+CIPSTART=\"TCP\",\"%s\",%d", g_server_ip, g_server_port);
        // "ALREADY CONNECTED" 里也含 "CONNECT" 子串，但那说明复用了一条
        // 来路不明的旧连接 —— 按失败处理，本轮循环重试时会先 CIPCLOSE
        // 超时 10s：目标不可达时模块自己的连接超时 >5s，太短会在它还在
        // 处理时就打断，下一轮全部撞上 "busy p..."，日志全是噪音
        if (WiFi_SendCmd(wifi_tx_buf, "CONNECT", 10000) &&
            strstr(wifi_rx_buf, "ALREADY") == NULL) {
            tcp_ok = 1;
        } else {
            if (wifi_rx_buf[0] != '\0') {
                DebugPrint("CIPSTART reply: ");
                HAL_UART_Transmit(&huart2, (uint8_t*)wifi_rx_buf,
                                  (uint16_t)strlen(wifi_rx_buf), 200);
                DebugPrint("\r\n");
            } else {
                DebugPrint("CIPSTART: no reply (IP unreachable / wrong network)\r\n");
            }
            HAL_Delay(1500);
        }
    }
    if (!tcp_ok) {
        HAL_UART_Transmit(&huart2, (uint8_t*)"TCP FAIL (4)\r\n", 15, 100);
        return 4;
    }
    HAL_UART_Transmit(&huart2, (uint8_t*)"TCP OK\r\n", 9, 100);
    // CIPSTART 返回 CONNECT 只能证明 socket 握手完成，模块内部数据通道
    // （发送缓冲区/滑动窗口）通常还需要 1~2 秒才完全就绪，
    // 第一次 CIPSEND 之前必须留足时间，否则会撞 busy s... 或无 > 响应。
    HAL_Delay(1500);

    // 5) 注册到 A 同学的服务端
    if (!DoRegister()) {
        HAL_UART_Transmit(&huart2, (uint8_t*)"REG FAIL (5)\r\n", 15, 100);
        return 5;
    }
    HAL_UART_Transmit(&huart2, (uint8_t*)"REG OK\r\n", 8, 100);

    // 6) 进入透传模式
    WiFi_SendCmd("AT+CIPMODE=1", "OK", 2000);
    WiFi_SendCmd("AT+CIPSEND", ">", 2000);
    HAL_UART_Transmit(&huart2, (uint8_t*)"Transparent Mode\r\n", 18, 100);

    // 7) 启动 USART1 接收中断（必须等透传建立后再开，否则启动期乱码会污染解析）
    //    注意顺序：先置 g_link_up = 1 再启动中断。若反过来，启动后~中断回调
    //    里看到 g_link_up == 0 会不重新挂接收，链路会"看着正常但收不到"。
    g_link_closed = 0;
    g_wifi_lost = 0;
    g_last_rx1_ms = HAL_GetTick();
    g_link_up = 1;
    HAL_UART_Receive_IT(&huart1, &rx1_byte, 1);

    return 0;
}

/* =====================================================================
 * 链路监视与恢复：A 踢掉连接（ESP8266 报 CLOSED）/ WiFi 掉线 / A 长时间
 * 静默时，重新走完整流程：
 *   退透传(+++) -> CIPMODE=0 -> (必要时重连 AP) -> CIPCLOSE -> CIPSTART
 *   -> DoRegister(重新注册!) -> CIPMODE=1 -> 再透传。
 * A 端只认"当前注册的连接"，所以重连后必须重新 REG，否则 HEART/YK/YT
 * 会被当成陌生连接的数据丢弃。
 * 返回：1 = 链路已恢复（含重新注册）；0 = 失败，主循环下一周期再试。
 * 注意：本函数会阻塞数秒（等待 AT 应答），期间 GUI 遥测暂停，
 * 这是裸机单线程下可接受的代价。
 * ===================================================================== */
static uint8_t LinkReconnect(void) {
    g_link_up = 0;               /* ISR 不再接管 USART1，AT 交换独占串口     */
    g_link_closed = 0;
    g_wifi_lost = 0;
    rx1_index = 0;
    rx1_line_ready = 0;
    /* 旧会话的待 ACK 命令已随旧连接作废，清空防止 CheckAckTimeout 空转重发 */
    memset((void*)g_ack_pending, 0, sizeof(g_ack_pending));
    HAL_UART_AbortReceive_IT(&huart1);
    DebugPrint("LINK LOST, reconnecting...\r\n");

    /* 重连目标地址上报（与 WiFi_Init 的 "TCP -> " 行一致）：
       界面"当前服务器"据此显示重连中的实际地址——尤其是 set_server
       改过地址后，这一行是"新地址已生效"的直接证据。 */
    {
        char tip[64];
        sprintf(tip, "TCP -> %s:%d\r\n", g_server_ip, g_server_port);
        DebugPrint(tip);
    }

    /* 退出透传"会话"（TCP 连接本身还挂着，后面 CIPCLOSE 负责关）。
       AT 固件要求 +++ 前后各 >1s 静默，否则不生效。 */
    LinkWaitYield(1100);
    UART1_Flush();
    HAL_UART_Transmit(&huart1, (uint8_t*)"+++", 3, 100);
    LinkWaitYield(1100);
    UART1_Flush();

    uint8_t ok = 0;
    for (int i = 0; i < 3 && !ok; i++) {
        /* 模块还活着吗（可能被重启/掉电，重启后需要等它起来） */
        if (!WiFi_SendCmd("AT", "OK", 2000)) { LinkWaitYield(1000); continue; }
        WiFi_SendCmd("AT+CIPMODE=0", "OK", 2000);

        /* AP 还连着吗？连着回 "+CWJAP:..."，没连回 "No AP"（匹配不到即没连） */
        if (!WiFi_SendCmd("AT+CWJAP?", "+CWJAP:", 2000)) {
            sprintf(wifi_tx_buf, "AT+CWJAP=\"%s\",\"%s\"", WIFI_SSID, WIFI_PASS);
            if (!WiFi_SendCmd(wifi_tx_buf, "GOT IP", 15000)) continue;
        }

        /* 关旧连接 -> 新建 TCP -> 重新注册（REG 成功 A 端才算我们在线） */
        WiFi_SendCmd("AT+CIPCLOSE", "CLOSED", 1000);   /* 无连接时回 ERROR，忽略 */
        UART1_Flush();
        sprintf(wifi_tx_buf, "AT+CIPSTART=\"TCP\",\"%s\",%d", g_server_ip, g_server_port);
        /* 超时 10s：太短会在模块还在处理上次 CIPSTART 时打断 -> busy p... */
        if (!WiFi_SendCmd(wifi_tx_buf, "CONNECT", 10000)) { LinkWaitYield(1000); continue; }
        /* "ALREADY CONNECTED" 防误判：关掉重连一次 */
        if (strstr(wifi_rx_buf, "ALREADY") != NULL) {
            WiFi_SendCmd("AT+CIPCLOSE", "CLOSED", 1000);
            UART1_Flush();
            if (!WiFi_SendCmd(wifi_tx_buf, "CONNECT", 10000)) continue;
        }
        LinkWaitYield(1500);           /* CIPSTART 后数据通道就绪需 1~2s（见 WiFi_Init） */
        if (!DoRegister()) { LinkWaitYield(1000); continue; }

        ok = 1;
    }

    if (!ok) {
        /* 三次都没成：硬复位模块（PA4 -> RST），下个主循环周期从头再来 */
        ESP_HardReset();
        LinkWaitYield(2000);
        DebugPrint("RECONNECT FAIL\r\n");
        return 0;
    }

    /* 重新进入透传 + 恢复接收中断。
       顺序关键：先 g_link_up = 1 再挂中断，否则中断回调看到 g_link_up == 0
       会拒绝重新挂接收，链路恢复后反而一个字节都收不到。 */
    WiFi_SendCmd("AT+CIPMODE=1", "OK", 2000);
    WiFi_SendCmd("AT+CIPSEND", ">", 2000);
    UART1_Flush();                 /* 丢弃交换期残留，防污染下一条 JSON 行 */
    g_last_rx1_ms = HAL_GetTick();
    g_link_up = 1;
    HAL_UART_Receive_IT(&huart1, &rx1_byte, 1);
    DebugPrint("RECONNECT OK\r\n");
    return 1;
}

// 应答帧统一用 cJSON 构造后整体发出（一行 JSON + '\n'）
static void SendConfigAck(cJSON *ack) {
    char *s = cJSON_PrintUnformatted(ack);
    cJSON_Delete(ack);
    if (s == NULL) return;
    uint32_t len = (uint32_t)strlen(s);
    s[len] = '\n';
    HAL_UART_Transmit(&huart2, (uint8_t*)s, len + 1, 200);
    cJSON_free(s);
}

// 解析上位机发来的 JSON 配置命令
void ParseConfigCommand(char *json_str) {
    cJSON *root = cJSON_Parse(json_str);
    if (root == NULL) {
        // 解析失败，回复 ERROR
        HAL_UART_Transmit(&huart2, (uint8_t*)"ERROR\r\n", 7, 100);
        return;
    }

    // 查看命令类型
    cJSON *cmd = cJSON_GetObjectItem(root, "cmd");
    if (cmd && cJSON_IsString(cmd) && strcmp(cmd->valuestring, "set_params") == 0) {
        // 提取各参数
        cJSON *cut_in = cJSON_GetObjectItem(root, "cut_in");
        cJSON *rated_wind = cJSON_GetObjectItem(root, "rated_wind");
        cJSON *cut_out = cJSON_GetObjectItem(root, "cut_out");
        cJSON *rated_power = cJSON_GetObjectItem(root, "rated_power");
        cJSON *mode = cJSON_GetObjectItem(root, "mode");

        // 更新全局变量（仅当字段存在时）
        if (cut_in && cJSON_IsNumber(cut_in)) g_wind_params.cut_in = cut_in->valuedouble;
        if (rated_wind && cJSON_IsNumber(rated_wind)) g_wind_params.rated_wind = rated_wind->valuedouble;
        if (cut_out && cJSON_IsNumber(cut_out)) g_wind_params.cut_out = cut_out->valuedouble;
        if (rated_power && cJSON_IsNumber(rated_power)) g_wind_params.rated_power = rated_power->valuedouble;
        if (mode && cJSON_IsNumber(mode)) g_control_mode = (uint8_t)mode->valuedouble;

        /* 评分表 35 项"串口回写及一致性"：不再只回 OK，而是把单片机
         * 实际生效的参数值回显给上位机，由上位机与界面显示值逐项比对。
         * 回的是更新后的 g_wind_params / g_control_mode —— 即单片机
         * 控制算法真正在用的值，不是简单地抄写收到的数字。 */
        cJSON *ack = cJSON_CreateObject();
        if (ack != NULL) {
            cJSON_AddStringToObject(ack, "cmd", "set_params_ack");
            cJSON_AddNumberToObject(ack, "cut_in", g_wind_params.cut_in);
            cJSON_AddNumberToObject(ack, "rated_wind", g_wind_params.rated_wind);
            cJSON_AddNumberToObject(ack, "cut_out", g_wind_params.cut_out);
            cJSON_AddNumberToObject(ack, "rated_power", g_wind_params.rated_power);
            cJSON_AddNumberToObject(ack, "mode", g_control_mode);
            SendConfigAck(ack);
        } else {
            HAL_UART_Transmit(&huart2, (uint8_t*)"OK\r\n", 4, 100);
        }
    } else if (cmd && cJSON_IsString(cmd) && strcmp(cmd->valuestring, "set_server") == 0) {
        /* 评分表 34 项"能在界面设置通信IP地址"：更新 A 端服务器地址并
         * 立即用新地址重连。回显实际生效的 ip/port 供上位机比对。 */
        cJSON *ip   = cJSON_GetObjectItem(root, "ip");
        cJSON *port = cJSON_GetObjectItem(root, "port");

        uint8_t valid = (ip && cJSON_IsString(ip) &&
                         ip->valuestring[0] != '\0' &&
                         strlen(ip->valuestring) < sizeof(g_server_ip));
        if (valid) {
            strcpy(g_server_ip, ip->valuestring);
            if (port && cJSON_IsNumber(port)) {
                int p = (int)port->valuedouble;
                if (p > 0 && p <= 65535) g_server_port = (uint16_t)p;
            }
            /* 置链路断开标志：主循环 3b 的监督条件会捕获它并走
             * LinkReconnect —— 那里用的就是刚更新的 g_server_ip/port。
             * 若链路本来就没建立，下一次重连同样用新地址。 */
            g_link_closed = 1;
        }

        cJSON *ack = cJSON_CreateObject();
        if (ack != NULL) {
            cJSON_AddStringToObject(ack, "cmd", "set_server_ack");
            cJSON_AddStringToObject(ack, "ip", g_server_ip);
            cJSON_AddNumberToObject(ack, "port", (double)g_server_port);
            cJSON_AddBoolToObject(ack, "applied", valid ? 1 : 0);
            SendConfigAck(ack);
        } else if (valid) {
            HAL_UART_Transmit(&huart2, (uint8_t*)"OK\r\n", 4, 100);
        } else {
            HAL_UART_Transmit(&huart2, (uint8_t*)"ERROR\r\n", 7, 100);
        }
    } else if (cmd && cJSON_IsString(cmd) && strcmp(cmd->valuestring, "get_server") == 0) {
        /* 评分表 34 项补强：查询固件当前实际使用的服务器地址。
         * 动机："TCP -> ip:port" 只在 WiFi_Init/LinkReconnect 时打印，
         * 上位机通常在固件联网之后才连上串口，那行早就错过了——
         * 界面"当前服务器"会一直停在"待固件上报"。
         * 上位机每次串口连接成功后主动查询一次，这里回显 g_server_ip/port
         * （含链路状态），界面随即显示固件此刻真正在用的地址。 */
        cJSON *ack = cJSON_CreateObject();
        if (ack != NULL) {
            cJSON_AddStringToObject(ack, "cmd", "get_server_ack");
            cJSON_AddStringToObject(ack, "ip", g_server_ip);
            cJSON_AddNumberToObject(ack, "port", (double)g_server_port);
            cJSON_AddBoolToObject(ack, "link_up", g_link_up ? 1 : 0);
            SendConfigAck(ack);
        } else {
            HAL_UART_Transmit(&huart2, (uint8_t*)"ERROR\r\n", 7, 100);
        }
    } else {
        // 未知命令
        HAL_UART_Transmit(&huart2, (uint8_t*)"UNKNOWN_CMD\r\n", 13, 100);
    }

    cJSON_Delete(root);
}
/* Small helper: print a debug string on USART2 (local monitor). */
static void DebugPrint(const char *s) {
    HAL_UART_Transmit(&huart2, (uint8_t*)s, (uint16_t)strlen(s), 200);
}

/* Service one pending GUI config command (USART2) if present.
 * Clears the "ready" flag BEFORE parsing, so a config line that completes during
 * parsing is still captured instead of being dropped. Safe ONLY in the main-loop
 * thread (ParseConfigCommand -> cJSON -> malloc). The ISR only accumulates into
 * rx2_line and sets the flag - it never allocates, so no heap race here. */
static void ServiceConfigNow(void) {
    if (rx2_line_ready) {
        rx2_line_ready = 0;
        ParseConfigCommand((char*)rx2_line);
    }
}

/* A "blocking" wait that still keeps the system responsive. While the link is
 * being re-established the main loop is stuck inside LinkReconnect, so a plain
 * HAL_Delay(N) would starve BOTH:
 *   - the GUI config command (it would arrive on USART2, sit in rx2_line_ready,
 *     and only be parsed after the whole reconnect finishes - long past the
 *     GUI's 5 s reply timeout -> "参数下发超时"); and
 *   - the GUI telemetry (the panel's wind/power/last-update freeze -> the
 *     user reads it as "串口服务器断连").
 * This wait services a pending config command every ~10 ms and refreshes the
 * GUI telemetry once a second. Both go out USART2, so they never disturb the
 * USART1 silence that the "+++" break-out sequence requires. */
static void LinkWaitYield(uint32_t ms) {
    uint32_t t0 = HAL_GetTick();
    while (HAL_GetTick() - t0 < ms) {
        HAL_Delay(10);
        ServiceConfigNow();
        if (HAL_GetTick() - g_last_gui_ms >= 1000) {
            g_last_gui_ms = HAL_GetTick();
            SendTelemetryToGUI(&wind_state);
        }
    }
}

/* Register a just-sent YK/YT so its ACK can be matched later.
 * interface.md 5.5: only one pending per (rtu,type,pt) - a repeat send
 * overwrites the old entry, the old command counts as discarded. */
static void AckPendingPut(const char *code, const char *type, int pt, double val) {
    AckPending_t *slot = NULL;
    for (int i = 0; i < 4; i++) {
        if (g_ack_pending[i].used &&
            strcmp(g_ack_pending[i].type, type) == 0 &&
            g_ack_pending[i].pt == pt) {
            slot = &g_ack_pending[i];            /* same point: overwrite */
            break;
        }
    }
    if (slot == NULL) {
        for (int i = 0; i < 4; i++) {
            if (!g_ack_pending[i].used) { slot = &g_ack_pending[i]; break; }
        }
    }
    if (slot == NULL) slot = &g_ack_pending[0]; /* all busy: drop oldest */
    slot->used = 1;
    strncpy(slot->code, code, 2); slot->code[2] = '\0';
    strncpy(slot->type, type, 2); slot->type[2] = '\0';
    slot->pt = pt;
    slot->val = val;
    slot->sent_ms = HAL_GetTick();
    slot->retries = 0;
}

/* Clear the pending slot matching (type,pt). Returns 1 if one was found. */
static uint8_t AckPendingClear(const char *type, int pt) {
    for (int i = 0; i < 4; i++) {
        if (g_ack_pending[i].used &&
            strcmp(g_ack_pending[i].type, type) == 0 &&
            g_ack_pending[i].pt == pt) {
            g_ack_pending[i].used = 0;
            return 1;
        }
    }
    return 0;
}

/* Unix-ms timestamp for outbound frames (interface.md 4.2: ts must be
 * int(time.time()*1000), NOT HAL_GetTick). */
static uint64_t NowUnixMs(void) {
    return (uint64_t)((int64_t)HAL_GetTick() + g_ts_offset);
}

/* Adopt A's clock: called with the ts of any inbound frame. Values that
 * cannot be a Unix-ms stamp (< Sep 2020) are ignored to avoid syncing on
 * garbage. Continuous re-sync also compensates MCU clock drift. */
static void SyncTimeFromA(double a_ts_ms) {
    if (a_ts_ms < 1600000000000.0) return;   /* not a Unix-ms stamp */
    g_ts_offset = (int64_t)a_ts_ms - (int64_t)HAL_GetTick();
}

/* Sanity-check a printed JSON frame BEFORE it goes on the wire. If the heap
 * were ever corrupted, cJSON would hand us garbage bytes (e.g. pointer values
 * >= 0x80) - that is exactly the "non UTF-8 frame" A complained about. Better
 * to drop the frame and log locally than to send garbage. */
static uint8_t FrameLooksJson(const char *s, uint32_t len) {
    return (len >= 2 && s[0] == '{' && s[len - 1] == '}');
}

/* Build one YK/YT frame with cJSON and send it over the transparent link.
 * cJSON renders 'val' as a real JSON number (floating point), which satisfies
 * interface.md 2.3 without relying on printf float support in the toolchain.
 * NOTE: there is no 'scale' field in the v0.3 Point structure - the old
 * integer+scale encoding would make A read values 100x too large. */
static void SendCmdFrame(const char *code, const char *type, int pt, double val) {
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) return;
    cJSON_AddStringToObject(root, "code", code);
    cJSON_AddNumberToObject(root, "ts", (double)NowUnixMs());
    cJSON_AddStringToObject(root, "src", "WT_CTRL");
    cJSON *data = cJSON_CreateObject();
    if (data != NULL) {
        cJSON_AddItemToObject(root, "data", data);
        cJSON_AddStringToObject(data, "rtu", "R01");
        cJSON_AddStringToObject(data, "type", type);
        cJSON_AddNumberToObject(data, "pt", pt);
        cJSON_AddNumberToObject(data, "val", val);
    }
    char *s = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (s == NULL) return;
    uint32_t len = (uint32_t)strlen(s);
    if (!FrameLooksJson(s, len)) {          /* heap corruption guard */
        cJSON_free(s);
        DebugPrint("TX FRAME CORRUPT, dropped\r\n");
        return;
    }
    s[len] = '\n';                 /* JSON Lines terminator, fits the NUL slot */
    HAL_UART_Transmit(&huart1, (uint8_t*)s, len + 1, 200);   /* to A          */
    HAL_UART_Transmit(&huart2, (uint8_t*)s, len + 1, 200);   /* debug mirror  */
    cJSON_free(s);
    AckPendingPut(code, type, pt, val);
}

/* Heartbeat frame, interface.md 4.3/5.2: {"code":"HEART","data":{"sim_time":N}} */
void SendHeartbeat(void) {
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) return;
    cJSON_AddStringToObject(root, "code", "HEART");
    cJSON_AddNumberToObject(root, "ts", (double)NowUnixMs());
    cJSON_AddStringToObject(root, "src", "WT_CTRL");
    cJSON *data = cJSON_CreateObject();
    if (data != NULL) {
        cJSON_AddItemToObject(root, "data", data);
        cJSON_AddNumberToObject(data, "sim_time", (double)sim_time);
    }
    char *s = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (s == NULL) return;
    uint32_t len = (uint32_t)strlen(s);
    if (!FrameLooksJson(s, len)) {          /* heap corruption guard */
        cJSON_free(s);
        DebugPrint("TX FRAME CORRUPT, dropped\r\n");
        return;
    }
    s[len] = '\n';
    HAL_UART_Transmit(&huart1, (uint8_t*)s, len + 1, 200);
    HAL_UART_Transmit(&huart2, (uint8_t*)s, len + 1, 200);   /* visibility    */
    cJSON_free(s);
}

/* Called from the main loop: YK/YT older than 2 s without ACK gets resent
 * once; still no answer after the resend -> give up and flag an error. */
void CheckAckTimeout(void) {
    uint32_t now = HAL_GetTick();
    for (int i = 0; i < 4; i++) {
        if (!g_ack_pending[i].used) continue;
        if (now - g_ack_pending[i].sent_ms < 2000) continue;
        if (g_ack_pending[i].retries == 0) {
            g_ack_pending[i].retries = 1;
            g_ack_pending[i].sent_ms = now;
            SendCmdFrame(g_ack_pending[i].code, g_ack_pending[i].type,
                         g_ack_pending[i].pt, g_ack_pending[i].val);
            /* SendCmdFrame -> AckPendingPut resets retries/sent_ms, so the
             * "already resent once" mark must be applied again afterwards,
             * otherwise this slot would retry forever every 2 s. */
            g_ack_pending[i].retries = 1;
            DebugPrint("ACK late -> resent\r\n");
        } else {
            g_ack_pending[i].used = 0;
            DebugPrint("ACK TIMEOUT, cmd failed\r\n");
        }
    }
}

/* PUSH 点表诊断：每个不同的 (type,pt) 组合只打一条 "A PUSH point: YC3"，
 * 用于核对 A 实际推送了哪些点（每秒一条 PUSH，逐条打会刷屏）。 */
static uint32_t g_push_seen[3];   /* [0]=YC [1]=YT [2]=YK，位掩码 pt1..pt31 */
static void LogPushPointOnce(const char *type, int pt) {
    int idx;
    if (type[0] != 'Y') return;
    if (type[1] == 'C')      idx = 0;
    else if (type[1] == 'T') idx = 1;
    else                     idx = 2;
    if (pt < 1 || pt > 31) return;
    uint32_t bit = 1u << (pt - 1);
    if (g_push_seen[idx] & bit) return;
    g_push_seen[idx] |= bit;
    char buf[40];
    sprintf(buf, "A PUSH point: %s%d\r\n", type, pt);
    DebugPrint(buf);
}

/* Handle one inbound JSON line from A (called from the MAIN loop, never the
 * ISR). Dispatch on 'code': PUSH -> update wind inputs; ACK/NACK -> clear the
 * matching pending command (interface.md 4.3). Registration ACK (state+role
 * only) has no type/pt and is simply ignored here. */
void ParseNetMessage(char *json_str) {
    cJSON *root = cJSON_Parse(json_str);
    if (root == NULL) {
        char snip[81];
        int n = (int)strlen(json_str);
        if (n > 70) n = 70;
        memcpy(snip, json_str, n);
        snip[n] = '\0';
        DebugPrint("PARSE_ERR: ");
        DebugPrint(snip);
        DebugPrint("\r\n");
        return;
    }

    cJSON *code = cJSON_GetObjectItem(root, "code");
    if (!code || !cJSON_IsString(code)) { cJSON_Delete(root); return; }
    const char *c = code->valuestring;

    /* A is the only clock source (rule 3): refresh our Unix-ms offset from
     * every inbound frame so our outbound ts stays aligned with A. */
    {
        cJSON *tsf = cJSON_GetObjectItem(root, "ts");
        if (tsf && cJSON_IsNumber(tsf)) SyncTimeFromA(tsf->valuedouble);
    }

    if (strcmp(c, "PUSH") == 0) {
        cJSON *data = cJSON_GetObjectItem(root, "data");
        if (!data) { cJSON_Delete(root); return; }
        cJSON *points = cJSON_GetObjectItem(data, "points");
        if (!points || !cJSON_IsArray(points)) { cJSON_Delete(root); return; }

        cJSON *point;
        cJSON_ArrayForEach(point, points) {
            cJSON *rtu  = cJSON_GetObjectItem(point, "rtu");
            cJSON *type = cJSON_GetObjectItem(point, "type");
            cJSON *pt   = cJSON_GetObjectItem(point, "pt");
            cJSON *val  = cJSON_GetObjectItem(point, "val");
            /* val:null + q:"BAD" fails cJSON_IsNumber -> bad points skipped */
            if (!rtu || !type || !pt || !val) continue;
            if (!cJSON_IsString(rtu) || !cJSON_IsString(type) ||
                !cJSON_IsNumber(pt) || !cJSON_IsNumber(val)) continue;
            if (strcmp(rtu->valuestring, "R01") != 0) continue;

            int pt_val = pt->valueint;
            float val_f = (float)val->valuedouble;

            LogPushPointOnce(type->valuestring, pt_val);  /* 点表核对诊断 */

            if (strcmp(type->valuestring, "YC") == 0 && pt_val == 1) {
                wind_state.wind_speed = val_f;  // 风速 W_SPD
            } else if (strcmp(type->valuestring, "YT") == 0 && pt_val == 1) {
                wind_state.power_setpoint = val_f;  // 功率设定 WT_P_SET
            } else if (strcmp(type->valuestring, "YC") == 0 && pt_val == 2) {
                g_a_output = val_f;               // 实际有功出力 WT_ACT（A 算）
                g_a_output_ms = HAL_GetTick();
            } else if (strcmp(type->valuestring, "YC") == 0 && pt_val == 3) {
                g_a_pitch = val_f;                // 实际桨距角 WT_PITCH（A 算）
                g_a_pitch_ms = HAL_GetTick();
            } else if (strcmp(type->valuestring, "YC") == 0 && pt_val == 4) {
                g_a_avail = val_f;                // 实际可用功率 WT_AVAIL（A 算）
                g_a_avail_ms = HAL_GetTick();
            }
            /* C 算出的桨距角/可用功率通过 YT1/YT2 上行回传给 A（见
             * SendControlToA），不再以 YC 形式反向发给 A。 */
        }
    } else if (strcmp(c, "ACK") == 0) {
        cJSON *data = cJSON_GetObjectItem(root, "data");
        cJSON *type = data ? cJSON_GetObjectItem(data, "type") : NULL;
        cJSON *pt   = data ? cJSON_GetObjectItem(data, "pt") : NULL;
        if (type && cJSON_IsString(type) && pt && cJSON_IsNumber(pt)) {
            if (AckPendingClear(type->valuestring, pt->valueint)) {
                DebugPrint("ACK OK\r\n");
            }
        }
    } else if (strcmp(c, "NACK") == 0) {
        cJSON *data = cJSON_GetObjectItem(root, "data");
        cJSON *type = data ? cJSON_GetObjectItem(data, "type") : NULL;
        cJSON *pt   = data ? cJSON_GetObjectItem(data, "pt") : NULL;
        if (type && cJSON_IsString(type) && pt && cJSON_IsNumber(pt)) {
            AckPendingClear(type->valuestring, pt->valueint);
        }
        DebugPrint("NACK from A\r\n");
    } else if (strcmp(c, "HEART") == 0) {
        /* A's heartbeat carries the authoritative sim_time (rule 3): adopt
         * it so our own HEART echoes A's simulation clock, not a local
         * counter that counts main-loop iterations. */
        cJSON *data = cJSON_GetObjectItem(root, "data");
        cJSON *st = data ? cJSON_GetObjectItem(data, "sim_time") : NULL;
        if (st && cJSON_IsNumber(st)) sim_time = (int)st->valuedouble;
    }
    /* anything else: nothing to do on the C side */

    cJSON_Delete(root);
}

// USART2 / USART1 接收中断回调（双串口各用一套缓冲区，互不打架）
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
    if (huart->Instance == USART2) {
        /* ISR does accumulation + snapshot ONLY (see the rx2_line comment up
         * top): calling ParseConfigCommand here raced the main-loop malloc
         * and corrupted the heap. */
        if (rx2_byte == '\n') {
            rx2_buffer[rx2_index] = '\0';
            if (rx2_buffer[0] == '{') {
                if (!rx2_line_ready) {
                    memcpy((void*)rx2_line, (void*)rx2_buffer, rx2_index + 1);
                    rx2_line_ready = 1;
                }                               /* else: main busy, line lost */
            }
            rx2_index = 0;
        } else {
            if (rx2_index < RX_BUF_SIZE - 1) rx2_buffer[rx2_index++] = rx2_byte;
            else rx2_index = 0;
        }
        HAL_UART_Receive_IT(&huart2, &rx2_byte, 1);
    } else if (huart->Instance == USART1) {
        /* LinkReconnect() owns USART1 while the link is down (blocking AT
           exchange). Swallow stray bytes and do NOT re-arm the IT. */
        if (!g_link_up) {
            return;
        }
        g_last_rx1_ms = HAL_GetTick();   /* any byte = A link still alive */
        /* ISR does accumulation + snapshot ONLY. cJSON parsing used to run
         * here and blocked the re-arm below, dropping inbound bytes; it now
         * happens in the main loop (ParseNetMessage). */
        if (rx1_byte == '\n') {
            rx1_buffer[rx1_index] = '\0';
            /* ESP8266 status words still come through in transparent mode.
               They arrive as "\r\nCLOSED\r\n": the leading \r forms a
               one-char line, "CLOSED\r" is the next one. */
            if (strncmp((char*)rx1_buffer, "CLOSED", 6) == 0) {
                g_link_closed = 1;              /* A kicked us off */
            } else if (strncmp((char*)rx1_buffer, "WIFI DISCONNECT", 15) == 0) {
                g_wifi_lost = 1;                /* AP link lost    */
            } else if (rx1_buffer[0] == '{') {
                if (!rx1_line_ready) {
                    memcpy((void*)rx1_line, (void*)rx1_buffer, rx1_index + 1);
                    rx1_line_ready = 1;
                } else {
                    rx1_lines_dropped++;   /* main loop still busy: line lost */
                }
            }
            rx1_index = 0;
        } else {
            if (rx1_index < RX1_BUF_SIZE - 1) rx1_buffer[rx1_index++] = rx1_byte;
            else rx1_index = 0;             /* oversize line: restart */
        }
        HAL_UART_Receive_IT(&huart1, &rx1_byte, 1);
    }
}

// 给 ESP8266 发送 AT 命令，等待指定回复。超时返回 0，成功返回 1。
/* Wait for one inbound TCP payload while in AT (non-transparent) mode.
 * The ESP8266 frames server data as "+IPD,<len>:<payload>", the payload
 * itself ending with '\n' (JSON Lines). Copies the whole "+IPD..." chunk
 * (without the trailing newline) into out and returns 1; 0 on timeout.
 * Replaces the old strstr("ACK") wait in DoRegister, which ALSO matched
 * the "ACK" inside "NACK" - a rejected REG looked like success, we then
 * sent HEART on an unregistered link and A kicked us, looping forever. */
static uint8_t WiFi_WaitIpdLine(char *out, uint16_t out_sz, uint32_t timeout_ms) {
    static char buf[320];            /* only used from the single main thread */
    uint16_t idx = 0;
    uint32_t t0 = HAL_GetTick();
    buf[0] = '\0';

    while (HAL_GetTick() - t0 < timeout_ms) {
        /* A config command that lands on USART2 during a long AT wait must be
           serviced here - the main loop is stuck in this AT exchange (CWJAP can
           block up to 15 s, CIPSTART 5 s), so without this a GUI save would
           time out. Same single-threaded safe pattern as LinkWaitYield. */
        ServiceConfigNow();
        uint8_t b;
        if (HAL_UART_Receive(&huart1, &b, 1, 50) != HAL_OK) continue;
        if (idx < sizeof(buf) - 1) buf[idx++] = (char)b;
        buf[idx] = '\0';
        char *ipd = strstr(buf, "+IPD,");
        if (ipd != NULL) {
            char *nl = strchr(ipd, '\n');
            if (nl != NULL) {                    /* payload fully arrived */
                size_t len = (size_t)(nl - ipd);
                if (len >= out_sz) return 0;
                memcpy(out, ipd, len);
                out[len] = '\0';
                return 1;
            }
        }
    }
    return 0;
}

uint8_t WiFi_SendCmd(char *cmd, char *ack, uint32_t timeout_ms) {
    // 清空接收缓冲区
    memset(wifi_rx_buf, 0, sizeof(wifi_rx_buf));

    // 关键修复：只有 cmd 非空时才发送，避免发送空字符串触发 ESP8266 误入透传
    if (cmd != NULL && cmd[0] != '\0') {
        sprintf(wifi_tx_buf, "%s\r\n", cmd);
        HAL_UART_Transmit(&huart1, (uint8_t*)wifi_tx_buf, strlen(wifi_tx_buf), 100);
    }
    // 如果 cmd 为空，不发送任何内容，只等待回复（用于 DoRegister 等等待 ACK）

    // 阻塞轮询接收直到超时
    uint32_t tick_start = HAL_GetTick();
    uint16_t rx_index = 0;

    while (HAL_GetTick() - tick_start < timeout_ms) {
        /* Keep servicing a pending GUI config command even during a long RTOS
           wait (e.g. CIPSEND waiting for ">", CWJAP 15 s, CIPSTART 5 s): the
           main loop is busy in this AT exchange, so a save would time out
           otherwise. Same single-threaded safe pattern as LinkWaitYield. */
        ServiceConfigNow();
        uint8_t byte;
        if (HAL_UART_Receive(&huart1, &byte, 1, 50) == HAL_OK) {
            wifi_rx_buf[rx_index++] = byte;
            if (rx_index >= sizeof(wifi_rx_buf) - 1) break;

            // 每收到一个字节就检查一次 ack：
            // ESP8266 的 ">" 提示符后面不带换行符，只在 \r/\n 时检查会漏掉它，
            // 导致缓冲区里明明有 ">" 却一直等到超时（CIPSEND 假失败）。
            if (strstr(wifi_rx_buf, ack) != NULL) {
                return 1; // 成功
            }
        }
    }
    return 0; // 超时失败
}
void SendControlToA(WindState_t *state) {
    /* YK1 = WT_START (0/1), self-judged from cut-in/cut-out wind speed.
     * Integral doubles print as integers, satisfying 2.3 for YK. */
    SendCmdFrame("YK", "YK", 1, (double)(state->run_status ? 1 : 0));

    /* 2026-09-09 protocol update from A: YT1 (WT_AVAIL) no longer requested.
     * 只发 YT2 = WT_PITCH_SET: 桨距角设定值(deg)，由风机控制器根据风速
     * + 功率闭环算出，回传电网（interface.md 2.3: YT val 是 JSON number，
     * 无 scale 字段）。Note: avail_power 仍在本地算，仅用于 GUI 镜像 YC4
     * 和本地显示，不通过 YT 上送给 A。 */
    SendCmdFrame("YT", "YT", 2, (double)state->pitch_angle);
}

/* Local monitor frame for the PC GUI (USART2 only, NOT sent to A).
 * Same points layout the GUI always used, but 'val' is now a real JSON
 * number (cJSON prints floating point) and there is no 'scale' field. */
static void SendTelemetryToGUI(WindState_t *state) {
    /* 所有 YC 都是 A→C：YC2/YC3/YC4 显示 A 推来的实际值（电网模拟器算出）。
     * 判据是"链路在线 + 收到过该点"，不再用 5 s 超时 —— A 的 PUSH 周期若大于
     * 5 s，超时会周期性把显示切回本地计算值，曲线看起来就是每隔几秒跳一下。
     * 链路断开时退回本地计算值（基于最后收到的风速/设定值）。 */
    double act = (g_link_up && g_a_output_ms != 0)
                 ? (double)g_a_output : (double)state->output_power;
    double pit = (g_link_up && g_a_pitch_ms != 0)
                 ? (double)g_a_pitch : (double)state->pitch_angle;
    double avl = (g_link_up && g_a_avail_ms != 0)
                 ? (double)g_a_avail : (double)state->avail_power;
    struct { const char *type; int pt; double val; } m[6] = {
        {"YC", 1, state->wind_speed},      /* W_SPD     */
        {"YC", 2, act},                    /* WT_ACT    */
        {"YC", 3, pit},                    /* WT_PITCH  */
        {"YC", 4, avl},                    /* WT_AVAIL  */
        {"YC", 5, state->power_setpoint},  /* WT_P_SET  */
        {"YX", 1, state->run_status ? 1.0 : 0.0}, /* WT_RUN */
    };

    cJSON *root = cJSON_CreateObject();
    cJSON *data = cJSON_CreateObject();
    cJSON *pts  = cJSON_CreateArray();
    if (root == NULL || data == NULL || pts == NULL) {
        cJSON_Delete(root); cJSON_Delete(data); cJSON_Delete(pts);
        return;
    }
    cJSON_AddStringToObject(root, "code", "PUSH");
    cJSON_AddNumberToObject(root, "ts", (double)HAL_GetTick());
    cJSON_AddStringToObject(root, "src", "WT_CTRL");
    cJSON_AddItemToObject(root, "data", data);
    cJSON_AddItemToObject(data, "points", pts);

    for (int i = 0; i < 6; i++) {
        cJSON *pt = cJSON_CreateObject();
        if (pt == NULL) continue;
        cJSON_AddStringToObject(pt, "rtu", "R01");
        cJSON_AddStringToObject(pt, "type", m[i].type);
        cJSON_AddNumberToObject(pt, "pt", m[i].pt);
        cJSON_AddNumberToObject(pt, "val", m[i].val);
        cJSON_AddItemToArray(pts, pt);
    }

    char *s = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (s == NULL) return;
    uint32_t len = (uint32_t)strlen(s);
    if (!FrameLooksJson(s, len)) {          /* heap corruption guard */
        cJSON_free(s);
        DebugPrint("TX FRAME CORRUPT, dropped\r\n");
        return;
    }
    s[len] = '\n';                            /* JSON Lines terminator */
    HAL_UART_Transmit(&huart2, (uint8_t*)s, len + 1, 200);
    cJSON_free(s);
}
// 注册流程（与旧 SendRegAndWaitAck 合并，旧函数已删除）
uint8_t DoRegister(void) {
    char reg_json[128];

    /* Two shots: at cold boot the clock is not yet synced to A, so the first
     * REG can carry a wrong ts and be NACKed. A's NACK itself carries a valid
     * ts - we sync from it and immediately retry with correct time. */
    for (int attempt = 0; attempt < 2; attempt++) {
        /* Build the REG frame with cJSON, NOT sprintf("%llu"): newlib-nano
         * printf has no 64-bit integer support, so %llu printed literally
         * as "lu" (see serial log: "ts":lu), producing invalid JSON that
         * A answered with NACK PARSE_ERR. cJSON's %1.15g path prints the
         * 13-digit Unix-ms timestamp correctly. */
        cJSON *frame = cJSON_CreateObject();
        cJSON *fdata = cJSON_CreateObject();
        if (frame == NULL || fdata == NULL) {
            cJSON_Delete(frame); cJSON_Delete(fdata);
            return 0;
        }
        cJSON_AddStringToObject(frame, "code", "REG");
        cJSON_AddNumberToObject(frame, "ts", (double)NowUnixMs());
        cJSON_AddStringToObject(frame, "src", "WT_CTRL");
        cJSON_AddItemToObject(frame, "data", fdata);
        cJSON_AddStringToObject(fdata, "role", "WT_CTRL");
        char *s = cJSON_PrintUnformatted(frame);
        cJSON_Delete(frame);
        if (s == NULL) return 0;
        uint32_t len = (uint32_t)strlen(s);
        s[len] = '\n';                         /* JSON Lines terminator */
        if (len + 1 > sizeof(reg_json)) {      /* frame must fit (75B < 128B) */
            cJSON_free(s);
            return 0;
        }
        memcpy(reg_json, s, len + 1);
        cJSON_free(s);

        //  注意：DoRegister 在 AT+CIPSTART 成功后调用，此时还没进入透传，
        // 所以需要用 AT+CIPSEND 发送数据
        char cmd[32];
        sprintf(cmd, "AT+CIPSEND=%d", (int)strlen(reg_json));
        if (!WiFi_SendCmd(cmd, ">", 5000)) {
            HAL_UART_Transmit(&huart2, (uint8_t*)"CIPSEND failed: ", 16, 100);
            HAL_UART_Transmit(&huart2, (uint8_t*)wifi_rx_buf, strlen(wifi_rx_buf), 100);
            HAL_UART_Transmit(&huart2, (uint8_t*)"\r\n", 2, 100);
            return 0;
        }

        HAL_UART_Transmit(&huart1, (uint8_t*)reg_json, strlen(reg_json), 100);
        HAL_UART_Transmit(&huart2, (uint8_t*)reg_json, strlen(reg_json), 100);

        /* Wait for A's reply (+IPD framed, see WiFi_WaitIpdLine). The old
         * code waited for strstr("ACK") which also matches "NACK". */
        static char reply[200];
        if (!WiFi_WaitIpdLine(reply, sizeof(reply), 5000)) {
            HAL_UART_Transmit(&huart2, (uint8_t*)"REG: no reply from A\r\n", 22, 100);
            return 0;
        }
        HAL_UART_Transmit(&huart2, (uint8_t*)"A reply: ", 9, 100);
        HAL_UART_Transmit(&huart2, (uint8_t*)reply, strlen(reply), 100);
        HAL_UART_Transmit(&huart2, (uint8_t*)"\r\n", 2, 100);

        char *json = strchr(reply, '{');
        if (json == NULL) {
            HAL_UART_Transmit(&huart2, (uint8_t*)"REG: reply has no JSON\r\n", 24, 100);
            return 0;
        }
        cJSON *root = cJSON_Parse(json);
        if (root == NULL) {
            HAL_UART_Transmit(&huart2, (uint8_t*)"REG: reply parse err\r\n", 22, 100);
            return 0;
        }

        /* Adopt A's clock from the reply ts - ACK and NACK both carry one. */
        cJSON *tsf = cJSON_GetObjectItem(root, "ts");
        if (tsf && cJSON_IsNumber(tsf)) SyncTimeFromA(tsf->valuedouble);

        cJSON *code = cJSON_GetObjectItem(root, "code");
        uint8_t is_ack = (code && cJSON_IsString(code) &&
                          strcmp(code->valuestring, "ACK") == 0);
        cJSON_Delete(root);

        if (is_ack) {
            HAL_UART_Transmit(&huart2, (uint8_t*)"ACK received!\r\n", 16, 100);
            return 1;
        }

        /* NACK or unexpected reply: the ts sync above has fixed our clock,
         * so one immediate retry should now register with a valid timestamp. */
        HAL_UART_Transmit(&huart2, (uint8_t*)"REG rejected, retry with synced ts\r\n", 36, 100);
    }
    return 0;
}
/* ---- crash diagnostics ------------------------------------------------
 * If the main loop ever dies silently (A times us out after 15 s but the
 * serial console shows nothing), the cause is almost certainly a HardFault
 * landing in the default while(1). These handlers print a marker over USART2
 * using RAW REGISTER ACCESS ONLY - no HAL, no locks, no interrupts - so they
 * work even from a faulted context. Seeing "HARDFAULT" on the console pins
 * the failure to firmware; NOT seeing it while A still reports silence
 * points to wiring/power on the ESP8266 link instead. */
static void FaultPrint(const char *s) {
    /* Guard: if the fault happened before USART2 was clocked (very early
     * init failure), touching its registers would fault again. */
    if ((RCC->APB1ENR1 & RCC_APB1ENR1_USART2EN) == 0) return;
    while (*s) {
        while ((USART2->ISR & USART_ISR_TXE) == 0) {}
        USART2->TDR = (uint8_t)*s++;
    }
    while ((USART2->ISR & USART_ISR_TC) == 0) {}
}

void FaultReport(void) {
    FaultPrint("\r\n!!! HARDFAULT !!!\r\n");
    __disable_irq();
    while (1) {}
}
/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  FaultPrint("\r\n!!! ERROR_HANDLER !!!\r\n");
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}

#ifdef  USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
