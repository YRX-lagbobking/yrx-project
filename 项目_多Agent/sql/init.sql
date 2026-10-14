-- =============================================================================
-- 智能健康管家多智能体系统 —— MySQL 8 初始化脚本（FR-11 / 需求文档 8.2）
-- =============================================================================
-- 执行方式（任选其一）：
--   1) 命令行：  mysql -uroot -p < sql/init.sql
--   2) 容器内：  docker exec -i health-mysql mysql -uroot -p1234 < sql/init.sql
--   3) 进入客户端后：SOURCE sql/init.sql;
--
-- 说明：
--   * 脚本可重复执行（先 DROP 再 CREATE）——生产环境请自行去掉 DROP 语句；
--   * 字符集统一 utf8mb4 / utf8mb4_general_ci（支持 emoji 与中文姓名）；
--   * `user` 是 MySQL 8 保留字，全部用反引号包裹；
--   * 生命体征时序数据不落在 MySQL，见 InfluxDB measurement `vital_signs`：
--       influxdb3 create database --retention-period 30d --token <TOKEN> agent_db
--
-- 默认账号（明文 → 密码哈希规则见文末"密码哈希说明"）：
--   u001 / u002 / u003  老人    明文 123456
--   f001 / f002         家属    明文 123456
--   admin               管理员  明文 admin123456
-- =============================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- 创建数据库
CREATE DATABASE IF NOT EXISTS `health_butler`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_general_ci;

USE `health_butler`;

-- -----------------------------------------------------------------------------
-- 1. 用户表（三种角色：elder 老人 / family 家属 / admin 管理员）
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `user`;
CREATE TABLE `user` (
    `user_id`     VARCHAR(32)  NOT NULL                COMMENT '用户编号，主键（u001/f001/admin）',
    `user_name`   VARCHAR(64)  NOT NULL                COMMENT '登录用户名，全局唯一',
    `password`    VARCHAR(128) NOT NULL DEFAULT ''     COMMENT '密码哈希：sha256("health_butler" + 明文)',
    `email`       VARCHAR(128) NOT NULL DEFAULT ''     COMMENT '邮箱（家属通知与用药提醒邮件使用）',
    `phone`       VARCHAR(20)  NOT NULL DEFAULT ''     COMMENT '手机号（脱敏后写日志）',
    `role`        VARCHAR(16)  NOT NULL DEFAULT 'elder' COMMENT '角色：elder 老人 / family 家属 / admin 管理员',
    `status`      TINYINT      NOT NULL DEFAULT 1      COMMENT '状态：1 正常 / 0 停用',
    `create_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `update_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`user_id`),
    UNIQUE KEY `uk_user_name` (`user_name`),
    KEY `idx_user_role` (`role`),
    KEY `idx_user_email` (`email`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '用户表（老人/家属/管理员）';

-- -----------------------------------------------------------------------------
-- 2. 家属-老人绑定关系表（一对多：一个家属可绑定多位老人）
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `elder_family`;
CREATE TABLE `elder_family` (
    `id`          INT          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    `elder_id`    VARCHAR(32)  NOT NULL                COMMENT '老人 user_id',
    `family_id`   VARCHAR(32)  NOT NULL                COMMENT '家属 user_id',
    `relation`    VARCHAR(32)  NOT NULL DEFAULT ''     COMMENT '关系：儿子/女儿/侄子等',
    `create_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '绑定时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_elder_family` (`elder_id`, `family_id`),
    KEY `idx_ef_family` (`family_id`),
    KEY `idx_ef_elder` (`elder_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '家属与老人的绑定关系';

-- -----------------------------------------------------------------------------
-- 3. 健康档案表（年龄/性别/慢病史/过敏史/紧急联系人）
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `health_profile`;
CREATE TABLE `health_profile` (
    `user_id`           VARCHAR(32)  NOT NULL            COMMENT '用户编号（一个用户一份档案）',
    `age`               INT          NOT NULL DEFAULT 0  COMMENT '年龄',
    `gender`            VARCHAR(8)   NOT NULL DEFAULT '' COMMENT '性别：男/女',
    `height`            DECIMAL(5,1) NOT NULL DEFAULT 0  COMMENT '身高 cm',
    `weight`            DECIMAL(5,1) NOT NULL DEFAULT 0  COMMENT '体重 kg',
    `disease_history`   VARCHAR(512) NOT NULL DEFAULT '' COMMENT '慢病史，逗号分隔：高血压,2型糖尿病',
    `allergy`           VARCHAR(256) NOT NULL DEFAULT '' COMMENT '过敏史',
    `emergency_contact` VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '紧急联系人姓名',
    `emergency_phone`   VARCHAR(20)  NOT NULL DEFAULT '' COMMENT '紧急联系人电话',
    `address`           VARCHAR(256) NOT NULL DEFAULT '' COMMENT '居住地址',
    `update_time`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`user_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '老人健康档案';

-- -----------------------------------------------------------------------------
-- 4. 用药计划表（药名/剂量/频次/服用时间/起止日期）
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `medication`;
CREATE TABLE `medication` (
    `id`         INT          NOT NULL AUTO_INCREMENT COMMENT '用药计划主键',
    `user_id`    VARCHAR(32)  NOT NULL                COMMENT '所属老人 user_id',
    `drug_name`  VARCHAR(64)  NOT NULL                COMMENT '药品名称',
    `dosage`     VARCHAR(64)  NOT NULL DEFAULT ''     COMMENT '单次剂量，如 1 片 / 5mg',
    `frequency`  VARCHAR(32)  NOT NULL DEFAULT ''     COMMENT '频次：每日一次/每日两次/每日三次',
    `take_time`  VARCHAR(64)  NOT NULL DEFAULT ''     COMMENT '服用时间，多个用逗号分隔：08:00,20:00',
    `start_date` DATE             NULL                COMMENT '开始日期',
    `end_date`   DATE             NULL                COMMENT '结束日期（NULL 表示长期）',
    `remark`     VARCHAR(256) NOT NULL DEFAULT ''     COMMENT '备注：饭前/饭后/忌口等',
    `status`     TINYINT      NOT NULL DEFAULT 1      COMMENT '状态：1 生效 / 0 停用',
    `create_time` DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    KEY `idx_medication_user` (`user_id`),
    KEY `idx_medication_drug` (`drug_name`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '用药计划';

-- -----------------------------------------------------------------------------
-- 5. 用药记录表（登记 已服 / 漏服 / 待服，用于依从性统计）
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `medication_record`;
CREATE TABLE `medication_record` (
    `id`            INT         NOT NULL AUTO_INCREMENT COMMENT '记录主键',
    `user_id`       VARCHAR(32) NOT NULL                COMMENT '所属老人 user_id',
    `medication_id` INT         NOT NULL                COMMENT '关联 medication.id',
    `plan_time`     VARCHAR(32) NOT NULL DEFAULT ''     COMMENT '计划服用时间：2026-09-21 08:00',
    `actual_time`   VARCHAR(32) NOT NULL DEFAULT ''     COMMENT '实际服用时间（未服为空）',
    `status`        VARCHAR(8)  NOT NULL DEFAULT '待服'  COMMENT '状态：待服 / 已服 / 漏服',
    `create_time`   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    KEY `idx_mr_user_time` (`user_id`, `plan_time`),
    KEY `idx_mr_medication` (`medication_id`),
    KEY `idx_mr_status` (`status`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '用药记录（依从性统计口径）';

-- -----------------------------------------------------------------------------
-- 6. 告警记录表（自动检测写入；手动检测不写——AC-10）
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `alert_record`;
CREATE TABLE `alert_record` (
    `id`          INT          NOT NULL AUTO_INCREMENT COMMENT '告警主键',
    `user_id`     VARCHAR(32)  NOT NULL                COMMENT '告警所属老人 user_id',
    `alert_type`  VARCHAR(64)  NOT NULL DEFAULT ''     COMMENT '异常类型：heart_rate_high / spo2_low 等',
    `level`       VARCHAR(8)   NOT NULL DEFAULT '低危'  COMMENT '级别：高危 / 中危 / 低危',
    `value`       VARCHAR(32)  NOT NULL DEFAULT ''     COMMENT '触发数值',
    `metric`      VARCHAR(32)  NOT NULL DEFAULT ''     COMMENT '指标名：heart_rate 等',
    `description` VARCHAR(512) NOT NULL DEFAULT ''     COMMENT '异常描述',
    `source`      VARCHAR(16)  NOT NULL DEFAULT 'auto' COMMENT '来源：auto 自动检测 / manual 手动检测',
    `status`      VARCHAR(8)   NOT NULL DEFAULT '待确认' COMMENT '状态：待确认 / 已确认',
    `notify_result` VARCHAR(256) NOT NULL DEFAULT ''   COMMENT '通知结果（短信/邮件/WS 通道留痕）',
    `create_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '告警时间',
    `confirm_time` DATETIME        NULL                COMMENT '家属确认时间',
    PRIMARY KEY (`id`),
    KEY `idx_alert_user_time` (`user_id`, `create_time`),
    KEY `idx_alert_level` (`level`),
    KEY `idx_alert_status` (`status`),
    KEY `idx_alert_source` (`source`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '健康告警记录';

-- -----------------------------------------------------------------------------
-- 7. 健康知识文档表（RAG 的元数据与关键词检索降级数据源）
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `knowledge_doc`;
CREATE TABLE `knowledge_doc` (
    `doc_id`       INT          NOT NULL AUTO_INCREMENT COMMENT '文档片段主键',
    `title`        VARCHAR(128) NOT NULL DEFAULT ''     COMMENT '文档标题',
    `source`       VARCHAR(128) NOT NULL DEFAULT ''     COMMENT '来源：《中国高血压防治指南》等',
    `version`      VARCHAR(32)  NOT NULL DEFAULT ''     COMMENT '版本：2023版',
    `disease_type` VARCHAR(32)  NOT NULL DEFAULT ''     COMMENT '病种：高血压/糖尿病/通用/用药',
    `page`         VARCHAR(16)  NOT NULL DEFAULT ''     COMMENT '页码或章节定位',
    `content`      TEXT         NOT NULL                COMMENT '正文片段（512 字符切分粒度）',
    `create_time`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
    PRIMARY KEY (`doc_id`),
    KEY `idx_kd_disease` (`disease_type`),
    KEY `idx_kd_source` (`source`)
    -- 如需全文检索可单独建（依赖 MySQL 内置 ngram 分词插件，部分镜像未启用）：
    --   ALTER TABLE `knowledge_doc` ADD FULLTEXT KEY `ft_kd_content` (`title`, `content`) WITH PARSER ngram;
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '健康知识库文档片段（RAG 溯源）';

-- -----------------------------------------------------------------------------
-- 8. 会话摘要表（L2 摘要记忆：Redis 不可用时的本地降级存储）
--    生产环境 L2 记忆由 PostgreSQL 承载（契约第 2 节），本表为同构降级表。
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS `conversation_summary`;
CREATE TABLE `conversation_summary` (
    `session_id`  VARCHAR(64)  NOT NULL COMMENT '会话编号（主键，与 Checkpointer thread_id 一致）',
    `user_id`     VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '会话归属用户',
    `summary`     TEXT         NOT NULL COMMENT '会话历史摘要',
    `rounds`      INT          NOT NULL DEFAULT 0  COMMENT '已摘要的对话轮数',
    `update_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`session_id`),
    KEY `idx_cs_user` (`user_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '会话摘要（L2 摘要记忆降级表）';

SET FOREIGN_KEY_CHECKS = 1;

-- =============================================================================
-- 初始化数据
-- =============================================================================

-- ---------- 1. 用户（3 老人 + 2 家属 + 1 管理员） ----------
INSERT INTO `user` (`user_id`, `user_name`, `password`, `email`, `phone`, `role`) VALUES
('u001', 'zhangsan', '2348b2c5fa68c689a3302e1c3edc241a937249507a0bd86720b78bee8512e9bd', 'zhangsan@example.com', '13800000001', 'elder'),
('u002', 'lisi',     '2348b2c5fa68c689a3302e1c3edc241a937249507a0bd86720b78bee8512e9bd', 'lisi@example.com',     '13800000002', 'elder'),
('u003', 'wangwu',   '2348b2c5fa68c689a3302e1c3edc241a937249507a0bd86720b78bee8512e9bd', 'wangwu@example.com',   '13800000003', 'elder'),
('f001', 'zhangxiaoming', '2348b2c5fa68c689a3302e1c3edc241a937249507a0bd86720b78bee8512e9bd', 'f001@example.com', '13900000001', 'family'),
('f002', 'lixiaohong',    '2348b2c5fa68c689a3302e1c3edc241a937249507a0bd86720b78bee8512e9bd', 'f002@example.com', '13900000002', 'family'),
('admin', 'admin', '0cabb86d16f431a16e35ea115de3cd04ccfeb029a0ff9626a3a859d6cec8ed95', 'admin@example.com', '13700000000', 'admin');

-- ---------- 2. 家属绑定关系（f001→u001, f002→u002；u003 暂未绑定，用于演示"未绑定返回 403"） ----------
INSERT INTO `elder_family` (`elder_id`, `family_id`, `relation`) VALUES
('u001', 'f001', '儿子'),
('u002', 'f002', '女儿');

-- ---------- 3. 健康档案 ----------
INSERT INTO `health_profile`
    (`user_id`, `age`, `gender`, `height`, `weight`, `disease_history`, `allergy`, `emergency_contact`, `emergency_phone`, `address`) VALUES
('u001', 72, '男', 170.0, 68.5, '高血压,2型糖尿病', '青霉素', '张小明', '13900000001', '北京市朝阳区幸福里 3 号楼 201'),
('u002', 68, '女', 158.0, 55.0, '高血压', '无', '李小红', '13900000002', '上海市静安区安远路 88 号 502'),
('u003', 75, '男', 172.0, 70.0, '冠心病,高血脂', '磺胺类', '王大力', '13900000003', '广州市天河区体育西路 12 号 1801');

-- ---------- 4. 用药计划（每位老人 2~3 条） ----------
INSERT INTO `medication`
    (`user_id`, `drug_name`, `dosage`, `frequency`, `take_time`, `start_date`, `end_date`, `remark`) VALUES
-- 张三（u001）：高血压 + 糖尿病
('u001', '苯磺酸氨氯地平片', '5mg（1 片）', '每日一次', '08:00', '2026-01-01', NULL, '早餐后服用，避免漏服'),
('u001', '二甲双胍缓释片',   '0.5g（1 片）', '每日两次', '08:00,18:00', '2026-01-01', NULL, '餐中或餐后服用，减少胃部不适'),
('u001', '阿司匹林肠溶片',   '100mg（1 片）', '每日一次', '20:00', '2026-03-01', NULL, '晚饭后服用，注意有无牙龈出血'),
-- 李四（u002）：高血压
('u002', '缬沙坦胶囊',       '80mg（1 粒）', '每日一次', '07:30', '2026-02-15', NULL, '晨起空腹服用'),
('u002', '硝苯地平控释片',   '30mg（1 片）', '每日一次', '19:00', NULL, NULL, '整片吞服，不可掰开'),
-- 王五（u003）：冠心病 + 高血脂
('u003', '阿托伐他汀钙片',   '20mg（1 片）', '每日一次', '21:00', '2025-11-01', NULL, '睡前服用，定期查肝功能'),
('u003', '单硝酸异山梨酯片', '20mg（1 片）', '每日两次', '08:00,16:00', '2025-11-01', NULL, '预防心绞痛发作');

-- ---------- 5. 用药记录（近 7 天，用于依从性统计） ----------
INSERT INTO `medication_record` (`user_id`, `medication_id`, `plan_time`, `actual_time`, `status`) VALUES
('u001', 1, '2026-09-15 08:00', '2026-09-15 08:10', '已服'),
('u001', 1, '2026-09-16 08:00', '2026-09-16 08:05', '已服'),
('u001', 1, '2026-09-17 08:00', '',                  '漏服'),
('u001', 1, '2026-09-18 08:00', '2026-09-18 08:20', '已服'),
('u001', 2, '2026-09-17 18:00', '2026-09-17 18:30', '已服'),
('u001', 2, '2026-09-18 18:00', '',                  '待服'),
('u001', 3, '2026-09-17 20:00', '2026-09-17 20:05', '已服'),
('u002', 4, '2026-09-17 07:30', '2026-09-17 07:40', '已服'),
('u002', 4, '2026-09-18 07:30', '2026-09-18 07:35', '已服'),
('u002', 5, '2026-09-17 19:00', '',                  '漏服'),
('u003', 6, '2026-09-17 21:00', '2026-09-17 21:10', '已服'),
('u003', 7, '2026-09-18 08:00', '2026-09-18 08:00', '已服');

-- ---------- 6. 告警记录（演示用：含来源区分，便于验证 AC-10） ----------
INSERT INTO `alert_record`
    (`user_id`, `alert_type`, `level`, `value`, `metric`, `description`, `source`, `status`, `notify_result`) VALUES
('u001', 'blood_pressure_high', '高危', '186', 'blood_pressure_sys', '收缩压达到 186 mmHg，超过安全范围 180 mmHg', 'auto', '待确认', 'MCP 短信已发送给 f001'),
('u001', 'trend_blood_pressure', '中危', '145', 'blood_pressure_sys', '近 7 天收缩压均值持续上升，偏离基线超过 30%', 'auto', '已确认', '邮件已发送给 f001'),
('u002', 'spo2_low', '中危', '89', 'spo2', '血氧饱和度 89%，低于 90% 阈值', 'auto', '待确认', ''),
('u002', 'heart_rate_high', '低危', '105', 'heart_rate', '心率 105 次/分，略高于日常水平', 'manual', '已确认', '手动检测不触发通知'),
('u003', 'glucose_high', '中危', '7.8', 'glucose', '空腹血糖 7.8 mmol/L，高于 7.0 mmol/L', 'auto', '待确认', '未绑定家属，落本地待发队列');

-- ---------- 7. 健康知识库（RAG 溯源：source / version / disease_type / page / content） ----------
INSERT INTO `knowledge_doc` (`title`, `source`, `version`, `disease_type`, `page`, `content`) VALUES
('高血压患者的日常血压管理',
 '中国高血压防治指南', '2024修订版', '高血压', '12',
 '老年高血压患者建议每日早晚各测量一次血压，测量前静坐 5 分钟，避免饮浓茶、咖啡与吸烟。收缩压持续高于 140 mmHg 时应及时就诊，由医生评估是否需要调整用药方案。家庭自测血压记录应连续保存，就诊时提供给医生参考。'),
('高血压的限盐与生活方式干预',
 '中国高血压防治指南', '2024修订版', '高血压', '18',
 '每日食盐摄入量建议控制在 5 克以内，减少咸菜、腌制品、酱料等高钠食物。增加新鲜蔬菜、水果与低脂奶制品摄入，每周进行 150 分钟中等强度有氧运动，如快步走、太极拳。控制体重、戒烟限酒对降低血压同样重要。'),
('高血压急症的识别与处置',
 '中国高血压防治指南', '2024修订版', '高血压', '31',
 '若血压突然显著升高并伴随剧烈头痛、视物模糊、胸痛、呼吸困难、意识改变，属于高血压急症，应立即拨打 120 急救电话，在等待期间保持安静半卧位，不要自行加服降压药，以免血压骤降导致脑供血不足。'),
('2 型糖尿病的饮食与血糖监测',
 '中国2型糖尿病防治指南', '2023版', '糖尿病', '22',
 '老年 2 型糖尿病患者建议空腹血糖控制在 7.0 mmol/L 以下，餐后 2 小时血糖控制在 10.0 mmol/L 以下。主食宜粗细搭配，定时定量，避免含糖饮料与高糖点心。每周至少监测 2~3 天血糖，包含空腹与餐后两个时间点。'),
('老年糖尿病患者低血糖的预防',
 '中国2型糖尿病防治指南', '2023版', '糖尿病', '27',
 '低血糖表现为心慌、出汗、手抖、饥饿感、头晕，严重时可出现意识模糊。空腹血糖低于 3.9 mmol/L 时应立即进食 15 克碳水化合物（如 3 块方糖、半杯果汁），15 分钟后复测。独居老人应随身携带糖块与写有病史的急救卡。'),
('老年人安全用药基本原则',
 '中国老年人用药安全专家共识', '2022版', '用药', '6',
 '老年人用药应遵循"受益原则、小剂量原则、五种药物原则、择时原则、暂停原则"。建议同时服用的药物不超过 5 种，服药时间固定并借助分药盒管理。漏服时若接近下次服药时间，跳过本次，切勿加倍补服。'),
('老年慢病患者的漏服处理与依从性',
 '中国老年人用药安全专家共识', '2022版', '用药', '15',
 '提高用药依从性的有效措施包括：使用一周分装药盒、设置手机或语音提醒、家属定期核对剩余药量。对于每日一次的降压药，若当天发现漏服且距下次服药时间超过 12 小时可补服一次；抗凝、降糖等治疗窗窄的药物漏服后应先咨询医生，不可自行补服。'),
('独居老人情绪低落的识别与陪伴',
 '老年人心理健康关怀手册', '2021版', '心理健康', '9',
 '独居老人出现情绪低落、兴趣减退、睡眠变差、反复诉说身体不适时，家属应增加联系频率，每天固定时间通话。陪伴时先倾听、不评判，避免"想开点""别多想"等否定性表达。若情绪低落持续两周以上并影响进食与睡眠，建议到老年科或心理科就诊。'),
('老年人居家跌倒与紧急呼救常识',
 '老年人居家安全指南', '2023版', '通用', '4',
 '居家环境应保持地面干燥、通道无障碍、夜间留有柔和照明，卫生间加装扶手与防滑垫。出现胸痛、呼吸困难、意识模糊、言语不清、一侧肢体无力等急性症状时，应立即拨打 120，并告知详细地址与既往病史。'),
('老年人心率与血氧的正常参考范围',
 '老年健康监测技术规范', '2022版', '通用', '11',
 '静息心率正常范围为 60~100 次/分，老年人略低属常见现象；持续低于 50 次/分或高于 120 次/分应就诊。静息状态血氧饱和度正常为 95%~100%，低于 90% 提示明显低氧，需尽快就医评估。测量时保持手指温暖、不要涂指甲油。');

-- =============================================================================
-- 密码哈希说明（与 app/core/security.py 的 hash_password 规则一致）
-- =============================================================================
--   规则：salt = "health_butler"
--         password = sha256(("health_butler" + 明文).encode("utf-8")).hexdigest()
--
--   明文 → 哈希对照表：
--   * 123456       → 2348b2c5fa68c689a3302e1c3edc241a937249507a0bd86720b78bee8512e9bd
--                    （u001 / u002 / u003 / f001 / f002 共用，明文均为 123456）
--   * admin123456  → 0cabb86d16f431a16e35ea115de3cd04ccfeb029a0ff9626a3a859d6cec8ed95
--
--   注意：u001/u002/u003/f001/f002 明文都是 123456，因此这 5 行哈希按规则必然相同，
--         这是 sha256 无逐用户随机盐的固有特性（本项目为教学/演示规模，生产应改用
--         bcrypt/argon2 + 每用户随机盐）。
--
--   校验方式（Python 3.11+）：
--     import hashlib
--     hashlib.sha256(("health_butler" + "123456").encode()).hexdigest()
-- =============================================================================
