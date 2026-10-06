//+------------------------------------------------------------------+
//|                                     MonetaDashboardBridge.mq5     |
//|  Read-only bridge: All History + live account + 4-symbol quotes   |
//|  Writes JSON to Terminal\Common\Files for the Flask dashboard.    |
//|  Does NOT trade. Attach to any chart on the logged-in account.    |
//+------------------------------------------------------------------+
#property copyright "Moneta Dashboard Bridge"
#property version   "1.00"
#property strict
#property description "Exports All History + live account/quotes for Prop Dashboard"

//=== Prop profile (aligned with dashboard 50k Phase I) ===
input double InpInitialBalance = 50000.0; // Initial balance
input double InpDailyLossUsd   = 2500.0;  // Daily loss limit USD
input double InpMaxLossUsd     = 5000.0;  // Max loss limit USD
input bool   InpTrailingMax    = false;   // Trailing max DD? (Instant=true)
input int    InpDayResetUTC    = 22;      // Trading-day reset hour UTC
input int    InpTimerSeconds   = 5;       // Refresh interval (seconds)

//=== Symbols + files ===
input string InpSymbols        = "EURUSD,GBPUSD,XAUUSD,EURCAD"; // Watchlist (comma-separated)
input string InpFileName       = "moneta_bridge.json";          // Common\\Files output
input bool   InpExportAllHistory = true;  // true = All History (ignore days/cap)
input int    InpHistoryDays    = 3650;    // Fallback window if All History off
input int    InpMaxDealsExport = 0;       // 0 = no cap; else last N OUT-related deals

//=== Telegram (optional — leave empty to disable) ===
input bool   InpUseTelegram    = false;
input string InpTgToken        = "";      // Bot token (do NOT hardcode secrets in git)
input string InpTgChatId       = "";
input double InpWarnPct        = 70.0;
input double InpDangerPct      = 90.0;
input int    InpFastTradeSec   = 30;
input bool   InpTgHeartbeat    = true;

//=== Globals ===
double   g_peak       = 0.0;
string   g_lvlDaily   = "ok";
string   g_lvlOverall = "ok";
bool     g_breach     = false;
datetime g_lastCloseSeen = 0;
datetime g_curDayB    = 0;
bool     g_started    = false;
int      g_writeCount = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   EventSetTimer(InpTimerSeconds < 2 ? 2 : InpTimerSeconds);
   g_peak          = AccountInfoDouble(ACCOUNT_EQUITY);
   g_lastCloseSeen = TimeGMT();
   g_curDayB       = DayBoundaryUTC();
   Print("MonetaDashboardBridge init → Common\\Files\\", InpFileName,
         " | AllHistory=", (InpExportAllHistory ? "yes" : "no"),
         " | symbols=", InpSymbols);
   ExportNow();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) { EventKillTimer(); }

void OnTimer()
{
   ExportNow();
   if(!g_started)
   {
      g_started = true;
      if(InpUseTelegram && InpTgHeartbeat)
         SendTelegram("✅ MonetaDashboardBridge ON | eq "
                    + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2));
   }
}

//+------------------------------------------------------------------+
datetime DayBoundaryUTC()
{
   datetime now = TimeGMT();
   MqlDateTime dt; TimeToStruct(now, dt);
   dt.hour = InpDayResetUTC; dt.min = 0; dt.sec = 0;
   datetime b = StructToTime(dt);
   if(now < b) b -= 86400;
   return b;
}

double TodayRealized()
{
   long     off = (long)(TimeTradeServer() - TimeGMT());
   datetime b   = (datetime)(DayBoundaryUTC() + off);
   if(!HistorySelect(b, TimeTradeServer() + 3600)) return 0.0;
   double s = 0.0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      long dtype = HistoryDealGetInteger(t, DEAL_TYPE);
      if(dtype != DEAL_TYPE_BUY && dtype != DEAL_TYPE_SELL) continue;
      s += HistoryDealGetDouble(t, DEAL_PROFIT)
         + HistoryDealGetDouble(t, DEAL_COMMISSION)
         + HistoryDealGetDouble(t, DEAL_SWAP);
   }
   return s;
}

string Level(double pct)
{
   if(pct >= InpDangerPct) return "danger";
   if(pct >= InpWarnPct)   return "warn";
   return "ok";
}

string JsonEsc(const string s)
{
   string o = s;
   StringReplace(o, "\\", "\\\\");
   StringReplace(o, "\"", "\\\"");
   StringReplace(o, "\r", " ");
   StringReplace(o, "\n", " ");
   return o;
}

//+------------------------------------------------------------------+
bool SelectHistoryWindow()
{
   datetime to = TimeTradeServer() + 3600;
   datetime from;
   if(InpExportAllHistory)
      from = D'2000.01.01';
   else
      from = (datetime)(TimeTradeServer() - (long)MathMax(1, InpHistoryDays) * 86400);
   if(!HistorySelect(from, to))
   {
      Print("HistorySelect failed err=", GetLastError(), " from=", from);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
void WriteQuotes(const int h)
{
   FileWriteString(h, "\"quotes\":{");
   string parts[];
   int n = StringSplit(InpSymbols, ',', parts);
   int written = 0;
   for(int i = 0; i < n; i++)
   {
      string sym = parts[i];
      StringTrimLeft(sym); StringTrimRight(sym);
      if(sym == "") continue;
      if(!SymbolSelect(sym, true))
      {
         Print("SymbolSelect failed: ", sym);
         continue;
      }
      MqlTick tick;
      if(!SymbolInfoTick(sym, tick))
      {
         Print("SymbolInfoTick failed: ", sym, " err=", GetLastError());
         continue;
      }
      double point = SymbolInfoDouble(sym, SYMBOL_POINT);
      int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double spread = (point > 0.0) ? (tick.ask - tick.bid) / point : 0.0;
      if(written > 0) FileWriteString(h, ",");
      FileWriteString(h,
         "\"" + JsonEsc(sym) + "\":{"
         + "\"bid\":" + DoubleToString(tick.bid, digits) + ","
         + "\"ask\":" + DoubleToString(tick.ask, digits) + ","
         + "\"last\":" + DoubleToString(tick.last, digits) + ","
         + "\"spread_points\":" + DoubleToString(spread, 1) + ","
         + "\"time\":" + IntegerToString((long)tick.time)
         + "}");
      written++;
   }
   FileWriteString(h, "},");
}

//+------------------------------------------------------------------+
void WritePositions(const int h)
{
   FileWriteString(h, "\"positions\":[");
   int pt = PositionsTotal();
   int cnt = 0;
   for(int i = 0; i < pt; i++)
   {
      string sym = PositionGetSymbol(i);
      if(sym == "") continue;
      long   ptype = PositionGetInteger(POSITION_TYPE);
      long   ticket = PositionGetInteger(POSITION_TICKET);
      datetime ot = (datetime)PositionGetInteger(POSITION_TIME);
      long dur = (long)(TimeCurrent() - ot);
      if(dur < 0) dur = 0;
      if(cnt > 0) FileWriteString(h, ",");
      FileWriteString(h,
         "{\"ticket\":" + IntegerToString(ticket) + ","
         + "\"symbol\":\"" + JsonEsc(sym) + "\","
         + "\"type\":\"" + (ptype == POSITION_TYPE_BUY ? "BUY" : "SELL") + "\","
         + "\"volume\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + ","
         + "\"price_open\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 5) + ","
         + "\"price_current\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), 5) + ","
         + "\"sl\":" + DoubleToString(PositionGetDouble(POSITION_SL), 5) + ","
         + "\"tp\":" + DoubleToString(PositionGetDouble(POSITION_TP), 5) + ","
         + "\"profit\":" + DoubleToString(PositionGetDouble(POSITION_PROFIT)
                                         + PositionGetDouble(POSITION_SWAP), 2) + ","
         + "\"duration_sec\":" + IntegerToString(dur) + ","
         + "\"comment\":\"" + JsonEsc(PositionGetString(POSITION_COMMENT)) + "\"}");
      cnt++;
   }
   FileWriteString(h, "],");
}

//+------------------------------------------------------------------+
void WriteDeals(const int h, datetime &newMaxClose)
{
   // Export ALL deal rows (balance/credit + trade IN/OUT) so dashboard can
   // rebuild closed trades exactly like Python history_deals_get.
   FileWriteString(h, "\"deals\":[");
   if(!SelectHistoryWindow())
   {
      FileWriteString(h, "],");
      FileWriteString(h, "\"deals_count\":0,");
      FileWriteString(h, "\"history_from\":\"\",");
      FileWriteString(h, "\"history_to\":\"\",");
      return;
   }

   int total = HistoryDealsTotal();
   int start = 0;
   if(InpMaxDealsExport > 0 && total > InpMaxDealsExport)
      start = total - InpMaxDealsExport;

   int written = 0;
   datetime histMin = 0;
   datetime histMax = 0;

   for(int i = start; i < total; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;

      datetime ct = (datetime)HistoryDealGetInteger(t, DEAL_TIME);
      long time_msc = (long)HistoryDealGetInteger(t, DEAL_TIME_MSC);
      if(time_msc <= 0) time_msc = (long)ct * 1000;

      string sym = HistoryDealGetString(t, DEAL_SYMBOL);
      long   dtype = HistoryDealGetInteger(t, DEAL_TYPE);
      long   entry = HistoryDealGetInteger(t, DEAL_ENTRY);
      long   posid = (long)HistoryDealGetInteger(t, DEAL_POSITION_ID);
      long   order = (long)HistoryDealGetInteger(t, DEAL_ORDER);

      if(written > 0) FileWriteString(h, ",");
      FileWriteString(h,
         "{\"ticket\":" + IntegerToString((long)t) + ","
         + "\"order\":" + IntegerToString(order) + ","
         + "\"position_id\":" + IntegerToString(posid) + ","
         + "\"time\":" + IntegerToString((long)ct) + ","
         + "\"time_msc\":" + IntegerToString(time_msc) + ","
         + "\"type\":" + IntegerToString(dtype) + ","
         + "\"entry\":" + IntegerToString(entry) + ","
         + "\"symbol\":\"" + JsonEsc(sym) + "\","
         + "\"volume\":" + DoubleToString(HistoryDealGetDouble(t, DEAL_VOLUME), 2) + ","
         + "\"price\":" + DoubleToString(HistoryDealGetDouble(t, DEAL_PRICE), 5) + ","
         + "\"profit\":" + DoubleToString(HistoryDealGetDouble(t, DEAL_PROFIT), 2) + ","
         + "\"swap\":" + DoubleToString(HistoryDealGetDouble(t, DEAL_SWAP), 2) + ","
         + "\"commission\":" + DoubleToString(HistoryDealGetDouble(t, DEAL_COMMISSION), 2) + ","
         + "\"comment\":\"" + JsonEsc(HistoryDealGetString(t, DEAL_COMMENT)) + "\"}");
      written++;

      if(histMin == 0 || ct < histMin) histMin = ct;
      if(ct > histMax) histMax = ct;

      // Fast-trade alert on fresh OUT deals only
      if((entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY) &&
         (dtype == DEAL_TYPE_BUY || dtype == DEAL_TYPE_SELL) &&
         ct > g_lastCloseSeen)
      {
         datetime ot = DealOpenTime(posid);
         if(ot == 0) ot = ct;
         long dur = (long)(ct - ot);
         if(dur < 0) dur = 0;
         if(dur < InpFastTradeSec)
         {
            double net = HistoryDealGetDouble(t, DEAL_PROFIT)
                       + HistoryDealGetDouble(t, DEAL_COMMISSION)
                       + HistoryDealGetDouble(t, DEAL_SWAP);
            string fm = "⚡ fast <" + IntegerToString(InpFastTradeSec) + "s | "
                      + sym + " | " + IntegerToString(dur) + "s | net "
                      + DoubleToString(net, 2);
            Print(fm);
            if(InpUseTelegram) SendTelegram(fm);
         }
         if(ct > newMaxClose) newMaxClose = ct;
      }
   }

   FileWriteString(h, "],");
   FileWriteString(h, "\"deals_count\":" + IntegerToString(written) + ",");
   FileWriteString(h, "\"history_from\":\"" + (histMin > 0 ? TimeToString(histMin, TIME_DATE) : "") + "\",");
   FileWriteString(h, "\"history_to\":\"" + (histMax > 0 ? TimeToString(histMax, TIME_DATE) : "") + "\",");
}

//+------------------------------------------------------------------+
datetime DealOpenTime(long posid)
{
   int total = HistoryDealsTotal();
   datetime best = 0;
   for(int i = 0; i < total; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      if((long)HistoryDealGetInteger(t, DEAL_POSITION_ID) != posid) continue;
      if(HistoryDealGetInteger(t, DEAL_ENTRY) == DEAL_ENTRY_IN)
      {
         datetime dt = (datetime)HistoryDealGetInteger(t, DEAL_TIME);
         if(best == 0 || dt < best) best = dt;
      }
   }
   return best;
}

//+------------------------------------------------------------------+
void ExportNow()
{
   double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
   double floating = AccountInfoDouble(ACCOUNT_PROFIT);
   string ccy      = AccountInfoString(ACCOUNT_CURRENCY);
   double todayReal = TodayRealized();

   double dailyLimit = InpDailyLossUsd;
   double sodBalance = balance - todayReal;
   double dailyFloor = sodBalance - dailyLimit;
   double dailyUsed  = MathMax(0.0, sodBalance - equity);
   double dailyPct   = (dailyLimit > 0) ? MathMin(100.0, dailyUsed / dailyLimit * 100.0) : 0.0;
   double dailyRoom  = equity - dailyFloor;

   double maxLimit = InpMaxLossUsd;
   double maxFloor, overallUsed;
   if(InpTrailingMax)
   {
      if(equity > g_peak) g_peak = equity;
      if(g_peak < InpInitialBalance) g_peak = InpInitialBalance;
      maxFloor    = g_peak - maxLimit;
      overallUsed = MathMax(0.0, g_peak - equity);
   }
   else
   {
      maxFloor    = InpInitialBalance - maxLimit;
      overallUsed = MathMax(0.0, InpInitialBalance - equity);
   }
   double overallPct  = (maxLimit > 0) ? MathMin(100.0, overallUsed / maxLimit * 100.0) : 0.0;
   double overallRoom = equity - maxFloor;
   bool   breached    = (equity <= dailyFloor) || (equity <= maxFloor);

   datetime newMaxClose = g_lastCloseSeen;

   int h = FileOpen(InpFileName, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
   {
      Print("FileOpen failed: ", GetLastError(), " → Common\\Files\\", InpFileName);
      return;
   }

   g_writeCount++;
   FileWriteString(h, "{");
   FileWriteString(h, "\"schema\":\"moneta_bridge_v1\",");
   FileWriteString(h, "\"ts\":" + IntegerToString((long)TimeGMT()) + ",");
   FileWriteString(h, "\"server_time\":" + IntegerToString((long)TimeTradeServer()) + ",");
   FileWriteString(h, "\"write_count\":" + IntegerToString(g_writeCount) + ",");
   FileWriteString(h, "\"source\":\"MonetaDashboardBridge\",");
   FileWriteString(h, "\"all_history\":" + (InpExportAllHistory ? "true" : "false") + ",");

   // Account live
   FileWriteString(h, "\"account\":{");
   FileWriteString(h, "\"login\":" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + ",");
   FileWriteString(h, "\"name\":\"" + JsonEsc(AccountInfoString(ACCOUNT_NAME)) + "\",");
   FileWriteString(h, "\"server\":\"" + JsonEsc(AccountInfoString(ACCOUNT_SERVER)) + "\",");
   FileWriteString(h, "\"company\":\"" + JsonEsc(AccountInfoString(ACCOUNT_COMPANY)) + "\",");
   FileWriteString(h, "\"currency\":\"" + JsonEsc(ccy) + "\",");
   FileWriteString(h, "\"balance\":" + DoubleToString(balance, 2) + ",");
   FileWriteString(h, "\"equity\":" + DoubleToString(equity, 2) + ",");
   FileWriteString(h, "\"profit\":" + DoubleToString(floating, 2) + ",");
   FileWriteString(h, "\"margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) + ",");
   FileWriteString(h, "\"margin_free\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) + ",");
   FileWriteString(h, "\"margin_level\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_LEVEL), 2) + ",");
   FileWriteString(h, "\"leverage\":" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LEVERAGE)));
   FileWriteString(h, "},");

   // Prop snapshot (EA-side quick view; dashboard recomputes from deals)
   FileWriteString(h, "\"prop\":{");
   FileWriteString(h, "\"initial_balance\":" + DoubleToString(InpInitialBalance, 2) + ",");
   FileWriteString(h, "\"daily_loss_usd\":" + DoubleToString(InpDailyLossUsd, 2) + ",");
   FileWriteString(h, "\"max_loss_usd\":" + DoubleToString(InpMaxLossUsd, 2) + ",");
   FileWriteString(h, "\"trailing\":" + (InpTrailingMax ? "true" : "false") + ",");
   FileWriteString(h, "\"day_reset_utc\":" + IntegerToString(InpDayResetUTC) + ",");
   FileWriteString(h, "\"today_realized\":" + DoubleToString(todayReal, 2) + ",");
   FileWriteString(h, "\"daily_floor\":" + DoubleToString(dailyFloor, 2) + ",");
   FileWriteString(h, "\"daily_room\":" + DoubleToString(dailyRoom, 2) + ",");
   FileWriteString(h, "\"daily_pct\":" + DoubleToString(dailyPct, 1) + ",");
   FileWriteString(h, "\"max_floor\":" + DoubleToString(maxFloor, 2) + ",");
   FileWriteString(h, "\"overall_room\":" + DoubleToString(overallRoom, 2) + ",");
   FileWriteString(h, "\"overall_pct\":" + DoubleToString(overallPct, 1) + ",");
   FileWriteString(h, "\"breached\":" + (breached ? "true" : "false"));
   FileWriteString(h, "},");

   WriteQuotes(h);
   WritePositions(h);
   WriteDeals(h, newMaxClose);

   FileWriteString(h, "\"ok\":true");
   FileWriteString(h, "}");
   FileClose(h);

   g_lastCloseSeen = newMaxClose;

   // Level-change alerts only
   string dl = Level(dailyPct);
   if(dl != g_lvlDaily)
   {
      g_lvlDaily = dl;
      if(dl != "ok")
      {
         string m = (dl == "danger" ? "RED" : "YEL") + " daily DD "
                  + DoubleToString(dailyPct, 0) + "% | room " + DoubleToString(dailyRoom, 2);
         Alert(m); if(InpUseTelegram) SendTelegram(m);
      }
   }
   string ol = Level(overallPct);
   if(ol != g_lvlOverall)
   {
      g_lvlOverall = ol;
      if(ol != "ok")
      {
         string m = (ol == "danger" ? "RED" : "YEL") + " max DD "
                  + DoubleToString(overallPct, 0) + "% | room " + DoubleToString(overallRoom, 2);
         Alert(m); if(InpUseTelegram) SendTelegram(m);
      }
   }
   if(breached && !g_breach)
   {
      g_breach = true;
      string m = "!! Prop rule breach risk — equity under floor !!";
      Alert(m); if(InpUseTelegram) SendTelegram(m);
   }
   else if(!breached) g_breach = false;

   datetime bNow = DayBoundaryUTC();
   if(g_curDayB == 0) g_curDayB = bNow;
   if(bNow != g_curDayB)
   {
      SendDailySummary(g_curDayB, bNow);
      g_curDayB  = bNow;
      g_lvlDaily = "ok";
   }
}

//+------------------------------------------------------------------+
void SendDailySummary(datetime from, datetime to)
{
   if(!(InpUseTelegram && InpTgHeartbeat)) return;
   if(!HistorySelect(from, to)) return;
   double pl = 0.0; int n = 0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      long e = HistoryDealGetInteger(t, DEAL_ENTRY);
      if(e == DEAL_ENTRY_OUT || e == DEAL_ENTRY_OUT_BY)
      {
         pl += HistoryDealGetDouble(t, DEAL_PROFIT)
             + HistoryDealGetDouble(t, DEAL_COMMISSION)
             + HistoryDealGetDouble(t, DEAL_SWAP);
         n++;
      }
   }
   SendTelegram("📊 day close | P/L " + DoubleToString(pl, 2)
              + " | trades " + IntegerToString(n)
              + " | eq " + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2));
}

//+------------------------------------------------------------------+
void SendTelegram(string text)
{
   if(!InpUseTelegram || InpTgToken == "" || InpTgChatId == "") return;
   string url  = "https://api.telegram.org/bot" + InpTgToken + "/sendMessage";
   string body = "chat_id=" + InpTgChatId + "&text=" + UrlEncode(text);

   uchar post[]; char result[]; string rheaders;
   int blen = StringToCharArray(body, post, 0, StringLen(body), CP_UTF8);
   if(blen > 0 && post[blen - 1] == 0) ArrayResize(post, blen - 1);

   string headers = "Content-Type: application/x-www-form-urlencoded\r\n";
   ResetLastError();
   int res = WebRequest("POST", url, headers, 5000, post, result, rheaders);
   if(res == -1)
      Print("WebRequest fail ", GetLastError(),
            " — allow https://api.telegram.org in Tools>Options>Expert Advisors");
}

string UrlEncode(string text)
{
   uchar bytes[];
   StringToCharArray(text, bytes, 0, WHOLE_ARRAY, CP_UTF8);
   string res = "";
   int sz = ArraySize(bytes);
   for(int i = 0; i < sz; i++)
   {
      uchar c = bytes[i];
      if(c == 0) break;
      if((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
         (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~')
         res += CharToString(c);
      else
         res += "%" + StringFormat("%02X", c);
   }
   return res;
}
//+------------------------------------------------------------------+
