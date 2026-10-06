//+------------------------------------------------------------------+
//| Order Level Control - Automated Prop Trading EA                  |
//| Version 2.31 (Simulated Time Engine & Target Close Exemption)   |
//+------------------------------------------------------------------+
#property strict
#property version   "2.31"
#property description "OLC V2.31: Prop Trading EA with Historical Simulation Time Guard, Hard Broker TP, and Prop Anti-Hedging."

#include <Trade/Trade.mqh>

CTrade trade;

//--------------------------- Strategy Modes -------------------------
enum ENUM_STRATEGY_MODE
{
   STRATEGY_NY_ORB = 0,        // Strategy 1: NY 15m ORB + Trend Filter (Best for XAUUSD)
   STRATEGY_ASIAN_SWEEP = 1,   // Strategy 2: Asian Liquidity Sweep (Best for EURUSD)
   STRATEGY_MANUAL_ONLY = 2    // Manual Panel Controls Only
};

// Which price-distance preset fills the panel / basket defaults
enum ENUM_SYMBOL_PRESET
{
   PRESET_AUTO   = 0,          // Detect from chart symbol (XAU* / EUR* / GBP* / else forex)
   PRESET_GOLD   = 1,          // Gold dollar distances
   PRESET_EURUSD = 2,          // EURUSD pip distances (dashboard defaults)
   PRESET_GBPUSD = 3,          // GBPUSD pip distances (dashboard defaults)
   PRESET_CUSTOM = 4           // Use "Basket Defaults (CUSTOM)" inputs only
};

//--------------------------- Inputs --------------------------------
input group "=== EA & Strategy Settings ==="
input ENUM_STRATEGY_MODE strategy_mode = STRATEGY_NY_ORB; // Active Strategy Engine
input ENUM_SYMBOL_PRESET symbol_preset = PRESET_AUTO;     // Basket distance preset
input long   magic_number              = 448015;
input bool   manage_current_symbol     = true;
input int    deviation_points          = 50;

input group "=== Prop Compliance & Anti-Hedging Guards ==="
input bool   anti_hedging_guard        = true; // Block Opposite-Direction Trades (Strict Prop Rule)
input double max_daily_loss_pct        = 2.00; // Daily Loss Hard-Stop Limit (%)
input int    min_trade_duration_sec    = 30;   // Min Duration for Early Manual Closes (Seconds)
input int    cooldown_minutes          = 5;    // Min Minutes Between Baskets

input group "=== Strategy Filters ==="
input int    h1_ema_period             = 50;   // H1 Trend Filter EMA Period
input double min_orb_range_usd         = 3.00; // Min Opening Range ($) for Gold

input group "=== Basket Defaults (CUSTOM / shared) ==="
input bool   default_fixed_mode        = false;
input double default_fixed_lot         = 0.02;
input double default_risk_percent      = 0.30; // Risk % per Basket
input double default_level_gap         = 5.00;
input double default_sl_gap            = 7.00;
input double default_profit_x          = 10.00;
input double default_be_offset         = 0.20;
input double default_partial_move      = 5.00;
input double default_partial_percent   = 50.00;
input double default_trail_distance    = 0.00;
input double default_runner_target     = 15.00;
input double default_emergency_loss    = 0.00;

input group "=== Preset GOLD (price units $) ==="
input double gold_level_gap            = 5.00;
input double gold_sl_gap               = 7.00;
input double gold_profit_x             = 10.00;
input double gold_be_offset            = 0.20;
input double gold_partial_move         = 5.00;
input double gold_runner_target        = 15.00;
input double gold_risk_percent         = 0.30;
input double gold_min_orb_range        = 3.00;

input group "=== Preset EURUSD (price units) ==="
input double eur_level_gap             = 0.0010;  // 10 pips
input double eur_sl_gap                = 0.0020;  // 20 pips (dashboard top)
input double eur_profit_x              = 0.0020;  // 20 pips
input double eur_be_offset             = 0.0001;
input double eur_partial_move          = 0.0010;
input double eur_runner_target         = 0.0030;
input double eur_risk_percent          = 0.50;

input group "=== Preset GBPUSD (price units) ==="
input double gbp_level_gap             = 0.0010;  // 10 pips
input double gbp_sl_gap                = 0.0020;  // 20 pips
input double gbp_profit_x              = 0.0030;  // 30 pips (dashboard top)
input double gbp_be_offset             = 0.0001;
input double gbp_partial_move          = 0.0010;
input double gbp_runner_target         = 0.0040;
input double gbp_risk_percent          = 0.50;

input group "=== UI Panel Position ==="
input int    panel_x                   = 40;
input int    panel_y                   = 40;

//--------------------------- Object Names ---------------------------
#define prefix              "OLC_V231_"
#define panel_bg            prefix"panel_bg"
#define header_bg           prefix"header_bg"
#define title_label         prefix"title"
#define sub_label           prefix"sub"

#define stat_pos            prefix"stat_pos"
#define stat_ord            prefix"stat_ord"
#define stat_profit         prefix"stat_profit"
#define stat_price          prefix"stat_price"
#define stat_spread         prefix"stat_spread"
#define status_label        prefix"status"
#define calc_lot_label      prefix"calc_lot"
#define daily_guard_label   prefix"daily_guard"

#define edit_lot            prefix"edit_lot"
#define edit_risk           prefix"edit_risk"
#define edit_level_gap      prefix"edit_level_gap"
#define edit_sl_gap         prefix"edit_sl_gap"
#define edit_profit_x       prefix"edit_profit_x"
#define edit_be_offset      prefix"edit_be_offset"
#define edit_partial_move   prefix"edit_partial_move"
#define edit_partial_percent prefix"edit_partial_percent"
#define edit_trail_distance prefix"edit_trail_distance"
#define edit_runner_target  prefix"edit_runner_target"
#define edit_emergency_loss prefix"edit_emergency_loss"

#define btn_mode            prefix"btn_mode"
#define btn_buy             prefix"btn_buy"
#define btn_sell            prefix"btn_sell"
#define btn_close           prefix"btn_close"

#define panel_w             370
#define panel_h             690
#define header_h            44

//--------------------------- Runtime State --------------------------
bool fixed_mode = false;
int  panel_left = 0;
int  panel_top = 0;
bool dragging = false;
int  last_mouse_x = 0;
int  last_mouse_y = 0;
int  group_seq = 0;

datetime last_basket_time = 0;
datetime current_day_start = 0;
double   daily_starting_balance = 0.0;
bool     daily_limit_breached = false;

// Strategy 1 (Asian Sweep) runtime variables
datetime last_asian_check = 0;
double   asian_high = 0.0;
double   asian_low = 0.0;

// Strategy 2 (NY ORB) runtime variables
datetime ny_range_date = 0;
double   ny_orb_high = 0.0;
double   ny_orb_low = 0.0;
bool     ny_orb_set = false;
int      handle_h1_ema = INVALID_HANDLE;

struct GroupState
{
   int    id;
   string dir;
   bool   pending_deleted;
   bool   first_be_done;
   bool   second_seen;
   bool   second_be_done;
   bool   first_partial_done;
   bool   second_partial_done;
};

GroupState groups[];

struct PositionMap
{
   ulong ticket;
   long  identifier;
   int   id;
   string dir;
   int   level;
};

PositionMap position_maps[];

//--------------------------- Symbol preset resolution ---------------
ENUM_SYMBOL_PRESET ResolvedPreset()
{
   if(symbol_preset != PRESET_AUTO)
      return symbol_preset;

   string s = _Symbol;
   StringToUpper(s);
   // strip common broker suffixes (EURUSD.a, EURUSD_i, ...)
   int dot = StringFind(s, ".");
   if(dot > 0) s = StringSubstr(s, 0, dot);
   int und = StringFind(s, "_");
   if(und > 0) s = StringSubstr(s, 0, und);

   if(s == "XAUUSD" || s == "GOLD")
      return PRESET_GOLD;
   if(s == "EURUSD")
      return PRESET_EURUSD;
   if(s == "GBPUSD")
      return PRESET_GBPUSD;
   // do NOT map JPY/crosses to EUR pip distances (100x wrong scale)
   return PRESET_CUSTOM;
}

bool PresetIsForexScale()
{
   ENUM_SYMBOL_PRESET p = ResolvedPreset();
   return (p == PRESET_EURUSD || p == PRESET_GBPUSD);
}

int PresetDigits()
{
   return PresetIsForexScale() ? 4 : 2;
}

double PresetLevelGap()
{
   switch(ResolvedPreset())
   {
      case PRESET_GOLD:   return gold_level_gap;
      case PRESET_EURUSD: return eur_level_gap;
      case PRESET_GBPUSD: return gbp_level_gap;
      default:            return default_level_gap;
   }
}

double PresetSLGap()
{
   switch(ResolvedPreset())
   {
      case PRESET_GOLD:   return gold_sl_gap;
      case PRESET_EURUSD: return eur_sl_gap;
      case PRESET_GBPUSD: return gbp_sl_gap;
      default:            return default_sl_gap;
   }
}

double PresetProfitX()
{
   switch(ResolvedPreset())
   {
      case PRESET_GOLD:   return gold_profit_x;
      case PRESET_EURUSD: return eur_profit_x;
      case PRESET_GBPUSD: return gbp_profit_x;
      default:            return default_profit_x;
   }
}

double PresetBEOffset()
{
   switch(ResolvedPreset())
   {
      case PRESET_GOLD:   return gold_be_offset;
      case PRESET_EURUSD: return eur_be_offset;
      case PRESET_GBPUSD: return gbp_be_offset;
      default:            return default_be_offset;
   }
}

double PresetPartialMove()
{
   switch(ResolvedPreset())
   {
      case PRESET_GOLD:   return gold_partial_move;
      case PRESET_EURUSD: return eur_partial_move;
      case PRESET_GBPUSD: return gbp_partial_move;
      default:            return default_partial_move;
   }
}

double PresetRunnerTarget()
{
   switch(ResolvedPreset())
   {
      case PRESET_GOLD:   return gold_runner_target;
      case PRESET_EURUSD: return eur_runner_target;
      case PRESET_GBPUSD: return gbp_runner_target;
      default:            return default_runner_target;
   }
}

double PresetRiskPercent()
{
   switch(ResolvedPreset())
   {
      case PRESET_GOLD:   return gold_risk_percent;
      case PRESET_EURUSD: return eur_risk_percent;
      case PRESET_GBPUSD: return gbp_risk_percent;
      default:            return default_risk_percent;
   }
}

double PresetMinOrbRange()
{
   if(ResolvedPreset() == PRESET_GOLD)
      return gold_min_orb_range;
   // FX / custom: dollar ORB filter is meaningless; disable min-range gate
   return 0.0;
}

string PresetName()
{
   switch(ResolvedPreset())
   {
      case PRESET_GOLD:   return "GOLD";
      case PRESET_EURUSD: return "EURUSD";
      case PRESET_GBPUSD: return "GBPUSD";
      case PRESET_CUSTOM: return "CUSTOM";
      default:            return "AUTO";
   }
}

//--------------------------- Anti-Hedging Guard ---------------------
bool HasOppositeDirection(ENUM_ORDER_TYPE proposed_dir)
{
   if(!anti_hedging_guard) return false;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      
      long pos_type = PositionGetInteger(POSITION_TYPE);
      if(proposed_dir == ORDER_TYPE_BUY && pos_type == POSITION_TYPE_SELL) return true;
      if(proposed_dir == ORDER_TYPE_SELL && pos_type == POSITION_TYPE_BUY) return true;
   }
   return false;
}

//--------------------------- Basic Helpers --------------------------
double MinLot() { return SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN); }
double MaxLot() { return SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX); }
double LotStep() { return SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP); }
double TickSize() { return SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE); }

double NormalizePrice(double price)
{
   double tick_size = TickSize();
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   if(tick_size <= 0.0) return NormalizeDouble(price, digits);
   double value = MathRound(price / tick_size) * tick_size;
   return NormalizeDouble(value, digits);
}

double NormalizeLotDown(double lot)
{
   double min_lot = MinLot();
   double max_lot = MaxLot();
   double step = LotStep();
   if(step <= 0.0) return lot;
   double value = MathFloor(lot / step + 1e-8) * step;
   if(value < min_lot - 1e-8) return 0.0;
   value = MathMin(value, max_lot);
   int digits = 2;
   if(step < 0.01) digits = 3;
   if(step < 0.001) digits = 4;
   return NormalizeDouble(value, digits);
}

double NormalizeLotNearest(double lot)
{
   double min_lot = MinLot();
   double max_lot = MaxLot();
   double step = LotStep();
   if(step <= 0.0) return lot;
   double value = MathRound(lot / step) * step;
   value = MathMax(min_lot, MathMin(value, max_lot));
   int digits = 2;
   if(step < 0.01) digits = 3;
   if(step < 0.001) digits = 4;
   return NormalizeDouble(value, digits);
}

double SpreadPrice()
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   return MathAbs(ask - bid);
}

string DirText(ENUM_ORDER_TYPE type) { return type == ORDER_TYPE_BUY ? "B" : "S"; }

int NewGroupId()
{
   group_seq++;
   int id = (int)((TimeLocal() % 100000) * 100 + group_seq % 100);
   if(id <= 0) id = MathRand() % 900000 + 100000;
   return id;
}

string MakeComment(int id, string dir, int level)
{
   return StringFormat("OLC:%d:%s:%d", id, dir, level);
}

bool ParseComment(string comment, int &id, string &dir, int &level)
{
   string parts[];
   ushort sep = StringGetCharacter(":", 0);
   int count = StringSplit(comment, sep, parts);
   if(count < 4 || parts[0] != "OLC") return false;
   id = (int)StringToInteger(parts[1]);
   dir = parts[2];
   level = (int)StringToInteger(parts[3]);
   if(id <= 0 || level <= 0) return false;
   if(dir != "B" && dir != "S") return false;
   return true;
}

int FindPositionMapByTicket(ulong ticket)
{
   for(int i = 0; i < ArraySize(position_maps); i++)
      if(position_maps[i].ticket == ticket) return i;
   return -1;
}

int FindPositionMapByIdentifier(long identifier)
{
   if(identifier <= 0) return -1;
   for(int i = 0; i < ArraySize(position_maps); i++)
      if(position_maps[i].identifier == identifier) return i;
   return -1;
}

void SavePositionMap(ulong ticket, long identifier, int id, string dir, int level)
{
   int index = FindPositionMapByIdentifier(identifier);
   if(index < 0) index = FindPositionMapByTicket(ticket);
   if(index < 0)
   {
      int n = ArraySize(position_maps);
      ArrayResize(position_maps, n + 1);
      index = n;
   }
   position_maps[index].ticket = ticket;
   position_maps[index].identifier = identifier;
   position_maps[index].id = id;
   position_maps[index].dir = dir;
   position_maps[index].level = level;
}

bool GetPositionMap(ulong ticket, long identifier, int &id, string &dir, int &level)
{
   int index = FindPositionMapByIdentifier(identifier);
   if(index < 0) index = FindPositionMapByTicket(ticket);
   if(index < 0) return false;
   id = position_maps[index].id;
   dir = position_maps[index].dir;
   level = position_maps[index].level;
   position_maps[index].ticket = ticket;
   position_maps[index].identifier = identifier;
   return true;
}

bool GetSelectedPositionGroupInfo(int &id, string &dir, int &level)
{
   ulong ticket = PositionGetInteger(POSITION_TICKET);
   long identifier = PositionGetInteger(POSITION_IDENTIFIER);
   string comment = PositionGetString(POSITION_COMMENT);
   if(ParseComment(comment, id, dir, level))
   {
      SavePositionMap(ticket, identifier, id, dir, level);
      return true;
   }
   if(GetPositionMap(ticket, identifier, id, dir, level)) return true;
   return false;
}

bool IsOurPositionByIndex(int index)
{
   ulong ticket = PositionGetTicket(index);
   if(ticket == 0) return false;
   if(manage_current_symbol && PositionGetString(POSITION_SYMBOL) != _Symbol) return false;
   if(PositionGetInteger(POSITION_MAGIC) != magic_number) return false;
   int id = 0; string dir = ""; int level = 0;
   return GetSelectedPositionGroupInfo(id, dir, level);
}

bool IsOurOrderByIndex(int index)
{
   ulong ticket = OrderGetTicket(index);
   if(ticket == 0) return false;
   if(manage_current_symbol && OrderGetString(ORDER_SYMBOL) != _Symbol) return false;
   if(OrderGetInteger(ORDER_MAGIC) != magic_number) return false;
   int id = 0; string dir = ""; int level = 0;
   string comment = OrderGetString(ORDER_COMMENT);
   return ParseComment(comment, id, dir, level);
}

//--------------------------- Smart Historical Time Duration Guard ----
bool CanClosePositionDuration(ulong ticket, bool is_target_close = false)
{
   if(is_target_close) return true; // Target / TP closes are EXEMPT from 30s guard!
   if(!PositionSelectByTicket(ticket)) return true;
   
   // TimeCurrent() in MT5 Backtest gives exact historical simulation tick time!
   datetime open_time = (datetime)PositionGetInteger(POSITION_TIME);
   datetime sim_now = TimeCurrent(); 
   int elapsed = (int)(sim_now - open_time);
   
   if(elapsed < min_trade_duration_sec)
   {
      Print(StringFormat("Hold Guard: Position %d open for %ds < %ds (Sim Time: %s). Delaying manual exit.", 
            ticket, elapsed, min_trade_duration_sec, TimeToString(sim_now, TIME_DATE|TIME_SECONDS)));
      return false;
   }
   return true;
}

//--------------------------- Prop 2% Daily Drawdown Hard Stop --------
void CheckDailyDrawdownGuard()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   datetime today_midnight = StructToTime(dt) - (dt.hour * 3600 + dt.min * 60 + dt.sec);

   if(today_midnight != current_day_start)
   {
      current_day_start = today_midnight;
      daily_starting_balance = AccountInfoDouble(ACCOUNT_BALANCE);
      daily_limit_breached = false;
   }

   double current_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double max_allowed_loss_usd = daily_starting_balance * (max_daily_loss_pct / 100.0);
   double current_daily_loss = daily_starting_balance - current_equity;

   if(current_daily_loss >= max_allowed_loss_usd)
   {
      if(!daily_limit_breached)
      {
         daily_limit_breached = true;
         Print(StringFormat("ALERT: Daily Max Loss Limit (%.2f%%) Breached! Current Loss: $%.2f. Closing all trades and stopping.", max_daily_loss_pct, current_daily_loss));
         CloseAllOlc();
      }
   }
}

//--------------------------- Panel Construction ----------------------
void StyleObject(string name, int z)
{
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_ZORDER, z);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial");
}

void Rect(string name, int x, int y, int w, int h, color bg, color border, int z)
{
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, border);
   StyleObject(name, z);
}

void Label(string name, string text, int x, int y, int w, int h, color clr, int font_size = 9)
{
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   StyleObject(name, 20);
}

void Edit(string name, string text, int x, int y, int w = 95, int h = 23)
{
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_EDIT, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clrGold);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, C'18,20,28');
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, C'70,70,80');
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
   ObjectSetInteger(0, name, OBJPROP_ALIGN, ALIGN_CENTER);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   StyleObject(name, 25);
}

void Button(string name, string text, int x, int y, int w, int h, color bg)
{
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clrWhite);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, clrBlack);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   StyleObject(name, 30);
}

double ReadDouble(string name, double fallback, bool allow_zero = false)
{
   string text = ObjectGetString(0, name, OBJPROP_TEXT);
   StringReplace(text, ",", ".");
   double value = StringToDouble(text);
   if(allow_zero) { if(value < 0.0) return fallback; }
   else { if(value <= 0.0) return fallback; }
   return value;
}

void SetText(string name, string text, color clr = clrSilver)
{
   if(ObjectFind(0, name) >= 0)
   {
      ObjectSetString(0, name, OBJPROP_TEXT, text);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   }
}

void CreatePanel()
{
   int x = panel_left;
   int y = panel_top;

   Rect(panel_bg, x, y, panel_w, panel_h, C'9,10,15', C'70,72,84', 5);
   Rect(header_bg, x + 6, y + 6, panel_w - 12, header_h, C'24,32,80', C'70,80,140', 15);

   Label(title_label, "OLC Prop EA V2.31", x + 16, y + 13, 280, 18, clrWhite, 11);
   Label(sub_label, StringFormat("Preset %s | Hard Broker TP", PresetName()), x + 16, y + 33, 280, 14, clrSilver, 7);

   int card_y = y + 64;
   int card_w = 102;
   int gap = 10;
   Rect(prefix"card1", x + 12, card_y, card_w, 52, C'20,22,30', C'45,48,60', 10);
   Rect(prefix"card2", x + 12 + card_w + gap, card_y, card_w, 52, C'20,22,30', C'45,48,60', 10);
   Rect(prefix"card3", x + 12 + (card_w + gap) * 2, card_y, card_w, 52, C'20,22,30', C'45,48,60', 10);

   Label(prefix"card1t", "Positions", x + 30, card_y + 8, 80, 14, clrGray, 8);
   Label(prefix"card2t", "Orders",    x + 148, card_y + 8, 80, 14, clrGray, 8);
   Label(prefix"card3t", "Profit",    x + 262, card_y + 8, 80, 14, clrGray, 8);
   Label(stat_pos, "0", x + 56, card_y + 25, 80, 20, clrWhite, 16);
   Label(stat_ord, "0", x + 168, card_y + 25, 80, 20, clrWhite, 16);
   Label(stat_profit, "0.00", x + 250, card_y + 25, 90, 20, clrLimeGreen, 12);

   int market_y = y + 124;
   Rect(prefix"market", x + 12, market_y, panel_w - 24, 48, C'20,22,30', C'45,48,60', 10);
   Label(prefix"pricet", "Price",  x + 75,  market_y + 7, 90, 12, clrGray, 8);
   Label(prefix"spreadt", "Spread", x + 245, market_y + 7, 90, 12, clrGray, 8);
   Label(stat_price, "0.00",  x + 60,  market_y + 23, 110, 18, clrWhite, 13);
   Label(stat_spread, "0.00", x + 250, market_y + 23, 80, 18, clrDodgerBlue, 13);

   int row = y + 188;
   int lx = x + 22;
   int ex = x + 245;
   int step = 29;

   Button(btn_mode, fixed_mode ? "MODE: FIXED LOT" : "MODE: RISK %", lx, row, panel_w - 44, 26, fixed_mode ? C'55,72,95' : C'72,55,95');
   row += step + 2;

   int dig = PresetDigits();

   Label(prefix"lot_lbl", "Fixed Lot", lx, row + 4, 160, 18, fixed_mode ? clrGainsboro : clrDimGray, 9);
   Edit(edit_lot, DoubleToString(default_fixed_lot, 2), ex, row, 95, 23);
   row += step;

   Label(prefix"risk_lbl", "Risk %", lx, row + 4, 160, 18, fixed_mode ? clrDimGray : clrGainsboro, 9);
   Edit(edit_risk, DoubleToString(PresetRiskPercent(), 2), ex, row, 95, 23);
   row += step;

   Label(prefix"gap_lbl", "Level Gap", lx, row + 4, 170, 18, clrGainsboro, 9);
   Edit(edit_level_gap, DoubleToString(PresetLevelGap(), dig), ex, row, 95, 23);
   row += step;

   Label(prefix"sl_lbl", "SL Gap + Spread", lx, row + 4, 180, 18, clrGainsboro, 9);
   Edit(edit_sl_gap, DoubleToString(PresetSLGap(), dig), ex, row, 95, 23);
   row += step;

   Label(prefix"x_lbl", "Profit X", lx, row + 4, 170, 18, clrGainsboro, 9);
   Edit(edit_profit_x, DoubleToString(PresetProfitX(), dig), ex, row, 95, 23);
   row += step;

   Label(prefix"be_lbl", "BE Offset", lx, row + 4, 170, 18, clrGainsboro, 9);
   Edit(edit_be_offset, DoubleToString(PresetBEOffset(), dig), ex, row, 95, 23);
   row += step;

   Label(prefix"pm_lbl", "Partial Move", lx, row + 4, 170, 18, clrGainsboro, 9);
   Edit(edit_partial_move, DoubleToString(PresetPartialMove(), dig), ex, row, 95, 23);
   row += step;

   Label(prefix"pc_lbl", "Partial Close %", lx, row + 4, 170, 18, clrGainsboro, 9);
   Edit(edit_partial_percent, DoubleToString(default_partial_percent, 0), ex, row, 95, 23);
   row += step;

   Label(prefix"trail_lbl", "Trail Distance", lx, row + 4, 170, 18, clrGainsboro, 9);
   Edit(edit_trail_distance, DoubleToString(default_trail_distance, dig), ex, row, 95, 23);
   row += step;

   Label(prefix"runner_lbl", "Runner Target", lx, row + 4, 170, 18, clrGainsboro, 9);
   Edit(edit_runner_target, DoubleToString(PresetRunnerTarget(), dig), ex, row, 95, 23);
   row += step;

   Label(prefix"emerg_lbl", "Emergency Loss $", lx, row + 4, 170, 18, clrGainsboro, 9);
   Edit(edit_emergency_loss, DoubleToString(default_emergency_loss, 2), ex, row, 95, 23);
   row += step + 10;

   Button(btn_buy,  "BUY",  x + 22, row, 150, 36, C'38,176,100');
   Button(btn_sell, "SELL", x + 198, row, 150, 36, C'220,65,65');
   row += 44;

   Button(btn_close, "CLOSE OLC", x + 22, row, panel_w - 44, 31, C'190,30,60');
   row += 38;

   Label(calc_lot_label, "Calculated Lot: 0.00", x + 22, row, panel_w - 44, 16, clrDodgerBlue, 9);
   row += 18;
   Label(daily_guard_label, "Daily Guard (2%): SAFE", x + 22, row, panel_w - 44, 16, clrSpringGreen, 8);
   row += 18;
   Label(status_label, "Status: ready", x + 22, row, panel_w - 44, 16, clrSilver, 8);

   ChartRedraw();
}

void DeletePanelObjects()
{
   int total = ObjectsTotal(0, 0, -1);
   for(int i = total - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i, 0, -1);
      if(StringFind(name, prefix) == 0)
         ObjectDelete(0, name);
   }
}

//--------------------------- Lot Calculation ------------------------
double RiskPerLot(ENUM_ORDER_TYPE direction, double entry, double stop)
{
   double profit = 0.0;
   if(!OrderCalcProfit(direction, _Symbol, 1.0, NormalizePrice(entry), NormalizePrice(stop), profit))
   {
      double tick_val = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tick_sz = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tick_sz > 0.0 && tick_val > 0.0)
         profit = (MathAbs(entry - stop) / tick_sz) * tick_val;
      else
         return 0.0;
   }
   return MathAbs(profit);
}

double CalculateLot(ENUM_ORDER_TYPE direction, double first_entry, double second_entry, double stop_price)
{
   if(fixed_mode) return NormalizeLotNearest(ReadDouble(edit_lot, default_fixed_lot));

   double risk_percent = ReadDouble(edit_risk, PresetRiskPercent());
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double max_risk = balance * risk_percent / 100.0;

   double r1 = RiskPerLot(direction, first_entry, stop_price);
   double r2 = RiskPerLot(direction, second_entry, stop_price);
   double total_risk_per_lot = r1 + r2;

   if(total_risk_per_lot <= 0.0 || max_risk <= 0.0) return 0.0;

   double lot = NormalizeLotDown(max_risk / total_risk_per_lot);
   if(lot <= 0.0)
   {
      double min_lot = MinLot();
      double min_risk = min_lot * total_risk_per_lot;
      if(min_risk <= max_risk + 1e-8) return min_lot;
      return 0.0;
   }

   return lot;
}

//--------------------------- Execution Functions --------------------
bool ClosePosition(ulong ticket, bool is_target_close = false)
{
   if(!CanClosePositionDuration(ticket, is_target_close)) return false;
   trade.SetExpertMagicNumber(magic_number);
   trade.SetDeviationInPoints(deviation_points);
   return trade.PositionClose(ticket);
}

bool DeleteOrder(ulong ticket)
{
   trade.SetExpertMagicNumber(magic_number);
   return trade.OrderDelete(ticket);
}

bool ModifyPosition(ulong ticket, double sl, double tp)
{
   if(!PositionSelectByTicket(ticket)) return false;
   MqlTradeRequest request; MqlTradeResult result;
   ZeroMemory(request); ZeroMemory(result);
   request.action = TRADE_ACTION_SLTP;
   request.position = ticket;
   request.symbol = PositionGetString(POSITION_SYMBOL);
   request.magic = magic_number;
   request.sl = NormalizePrice(sl);
   request.tp = NormalizePrice(tp);
   return OrderSend(request, result) && result.retcode == TRADE_RETCODE_DONE;
}

bool OpenMarket(ENUM_ORDER_TYPE direction, double lot, double stop_price, double profit_x, int group_id)
{
   string dir = DirText(direction);
   string comment = MakeComment(group_id, dir, 1);
   trade.SetExpertMagicNumber(magic_number);
   trade.SetDeviationInPoints(deviation_points);

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double entry = direction == ORDER_TYPE_BUY ? ask : bid;
   double tp_price = direction == ORDER_TYPE_BUY ? entry + profit_x : entry - profit_x;

   if(direction == ORDER_TYPE_BUY)
      return trade.Buy(lot, _Symbol, 0.0, NormalizePrice(stop_price), NormalizePrice(tp_price), comment);
   else
      return trade.Sell(lot, _Symbol, 0.0, NormalizePrice(stop_price), NormalizePrice(tp_price), comment);
}

bool PlaceSecondLimit(ENUM_ORDER_TYPE direction, double lot, double second_entry, double first_entry, double stop_price, int group_id)
{
   MqlTradeRequest request; MqlTradeResult result;
   ZeroMemory(request); ZeroMemory(result);
   string dir = DirText(direction);
   request.action = TRADE_ACTION_PENDING;
   request.symbol = _Symbol;
   request.magic = magic_number;
   request.volume = lot;
   request.price = NormalizePrice(second_entry);
   request.sl = NormalizePrice(stop_price);
   request.tp = NormalizePrice(first_entry);
   request.deviation = deviation_points;
   request.type_time = ORDER_TIME_GTC;
   request.type_filling = ORDER_FILLING_RETURN;
   request.comment = MakeComment(group_id, dir, 2);
   request.type = direction == ORDER_TYPE_BUY ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT;

   bool ok = OrderSend(request, result);
   return ok && (result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED);
}

void StartBasket(ENUM_ORDER_TYPE direction)
{
   if(daily_limit_breached)
   {
      SetText(status_label, "Trading BLOCKED: Daily 2% Loss Breached!", clrTomato);
      return;
   }

   if(HasOppositeDirection(direction))
   {
      SetText(status_label, "Anti-Hedging Guard: Blocked opposite trade!", clrGold);
      return;
   }

   if(last_basket_time > 0 && (TimeCurrent() - last_basket_time) < (cooldown_minutes * 60))
   {
      SetText(status_label, StringFormat("Cooldown active (%dm min gap)", cooldown_minutes), clrGold);
      return;
   }

   double level_gap = ReadDouble(edit_level_gap, PresetLevelGap());
   double sl_gap = ReadDouble(edit_sl_gap, PresetSLGap());
   double profit_x = ReadDouble(edit_profit_x, PresetProfitX());
   double spread = SpreadPrice();

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   double first_entry = direction == ORDER_TYPE_BUY ? ask : bid;
   double second_entry = direction == ORDER_TYPE_BUY ? first_entry - level_gap : first_entry + level_gap;
   double stop_price = direction == ORDER_TYPE_BUY ? second_entry - sl_gap - spread : second_entry + sl_gap + spread;

   first_entry = NormalizePrice(first_entry);
   second_entry = NormalizePrice(second_entry);
   stop_price = NormalizePrice(stop_price);

   double lot = CalculateLot(direction, first_entry, second_entry, stop_price);
   if(lot <= 0.0) return;

   int group_id = NewGroupId();
   string dir = DirText(direction);
   AddGroup(group_id, dir);

   if(!OpenMarket(direction, lot, stop_price, profit_x, group_id))
   {
      SetText(status_label, "Basket aborted: market entry failed", clrTomato);
      return;
   }
   if(!PlaceSecondLimit(direction, lot, second_entry, first_entry, stop_price, group_id))
   {
      // keep level-1 live but flag incomplete basket (no orphan risk yet)
      SetText(status_label, StringFormat("Basket %d: L1 open, L2 limit FAILED", group_id), clrGold);
      PrintFormat("Basket %d: PlaceSecondLimit failed after L1 fill", group_id);
   }
   else
      SetText(status_label, StringFormat("Basket %d created with Hard TP", group_id), clrLightGreen);

   last_basket_time = TimeCurrent();
   SetText(calc_lot_label, StringFormat("Calculated Lot: %.2f", lot), clrDodgerBlue);
}

void CloseAllOlc()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!IsOurPositionByIndex(i)) continue;
      ulong ticket = PositionGetInteger(POSITION_TICKET);
      ClosePosition(ticket, true);
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!IsOurOrderByIndex(i)) continue;
      ulong ticket = OrderGetInteger(ORDER_TICKET);
      DeleteOrder(ticket);
   }
}

//--------------------------- Strategy 1: NY 15m ORB Engine -------------
void RunStrategy_NY_ORB()
{
   if(strategy_mode != STRATEGY_NY_ORB) return;
   if(daily_limit_breached) return;

   MqlDateTime dt;
   TimeCurrent(dt);

   if(dt.hour == 13 && dt.min >= 45 && !ny_orb_set && dt.day != ny_range_date)
   {
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      if(CopyRates(_Symbol, PERIOD_M15, 0, 5, rates) >= 2)
      {
         ny_orb_high = rates[1].high;
         ny_orb_low = rates[1].low;
         double orb_range = ny_orb_high - ny_orb_low;

         if(orb_range >= PresetMinOrbRange())
         {
            ny_orb_set = true;
            ny_range_date = dt.day;
            Print(StringFormat("NY ORB Set for Day %d: High=%.2f, Low=%.2f (Range: $%.2f)", dt.day, ny_orb_high, ny_orb_low, orb_range));
         }
      }
   }

   if(ny_orb_set && dt.hour >= 13 && dt.hour <= 15)
   {
      MqlRates m5_rates[];
      ArraySetAsSeries(m5_rates, true);

      double ema_vals[];
      ArraySetAsSeries(ema_vals, true);
      double current_ema = 0.0;
      if(handle_h1_ema != INVALID_HANDLE && CopyBuffer(handle_h1_ema, 0, 0, 2, ema_vals) >= 1)
         current_ema = ema_vals[0];

      if(CopyRates(_Symbol, PERIOD_M5, 0, 3, m5_rates) >= 2)
      {
         if(m5_rates[1].close > ny_orb_high && m5_rates[2].close <= ny_orb_high && (current_ema == 0.0 || m5_rates[1].close > current_ema))
         {
            Print("NY ORB Strategy: Buy Breakout Triggered (Trend Aligned)!");
            StartBasket(ORDER_TYPE_BUY);
            ny_orb_set = false;
         }
         else if(m5_rates[1].close < ny_orb_low && m5_rates[2].close >= ny_orb_low && (current_ema == 0.0 || m5_rates[1].close < current_ema))
         {
            Print("NY ORB Strategy: Sell Breakout Triggered (Trend Aligned)!");
            StartBasket(ORDER_TYPE_SELL);
            ny_orb_set = false;
         }
      }
   }
}

//--------------------------- Strategy 2: Asian Liquidity Sweep Engine -
void RunStrategy_AsianSweep()
{
   if(strategy_mode != STRATEGY_ASIAN_SWEEP) return;
   if(daily_limit_breached) return;

   MqlDateTime dt;
   TimeCurrent(dt);

   if(dt.hour == 7 && dt.min <= 5 && dt.day != last_asian_check)
   {
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int copied = CopyRates(_Symbol, PERIOD_H1, 0, 10, rates);
      if(copied >= 8)
      {
         double highest = 0.0;
         double lowest = 999999.0;
         for(int i = 1; i <= 7; i++)
         {
            if(rates[i].high > highest) highest = rates[i].high;
            if(rates[i].low < lowest) lowest = rates[i].low;
         }
         asian_high = highest;
         asian_low = lowest;
         last_asian_check = dt.day;
         Print(StringFormat("Asian Sweep Range Set: High=%.5f, Low=%.5f", asian_high, asian_low));
      }
   }

   if(asian_high > 0.0 && dt.hour >= 7 && dt.hour <= 10)
   {
      MqlRates m5_rates[];
      ArraySetAsSeries(m5_rates, true);
      if(CopyRates(_Symbol, PERIOD_M5, 0, 3, m5_rates) >= 2)
      {
         if(m5_rates[2].low < asian_low && m5_rates[1].close > asian_low && m5_rates[1].close > m5_rates[1].open)
         {
            Print("Asian Sweep Strategy: Low Sweep & Bullish Reversal -> BUY!");
            StartBasket(ORDER_TYPE_BUY);
            asian_high = 0.0;
         }
         else if(m5_rates[2].high > asian_high && m5_rates[1].close < asian_high && m5_rates[1].close < m5_rates[1].open)
         {
            Print("Asian Sweep Strategy: High Sweep & Bearish Reversal -> SELL!");
            StartBasket(ORDER_TYPE_SELL);
            asian_high = 0.0;
         }
      }
   }
}

//--------------------------- Basket Helper Functions ----------------
int FindGroup(int id, string dir)
{
   for(int i = 0; i < ArraySize(groups); i++)
      if(groups[i].id == id && groups[i].dir == dir) return i;
   return -1;
}

int AddGroup(int id, string dir)
{
   int index = FindGroup(id, dir);
   if(index >= 0) return index;
   int n = ArraySize(groups);
   ArrayResize(groups, n + 1);
   groups[n].id = id; groups[n].dir = dir;
   groups[n].pending_deleted = false; groups[n].first_be_done = false;
   groups[n].second_seen = false; groups[n].second_be_done = false;
   groups[n].first_partial_done = false; groups[n].second_partial_done = false;
   return n;
}

bool HasOpenLevel(const int id, const string dir, const int level)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!IsOurPositionByIndex(i)) continue;
      int pid = 0; string pdir = ""; int plevel = 0;
      if(!GetSelectedPositionGroupInfo(pid, pdir, plevel)) continue;
      if(pid == id && pdir == dir && plevel == level)
         return true;
   }
   return false;
}

// Cancel orphan level-2 pendings when level-1 of the same basket is gone.
void ManageBasketPendings()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!IsOurOrderByIndex(i)) continue;
      ulong ticket = OrderGetInteger(ORDER_TICKET);
      int id = 0; string dir = ""; int level = 0;
      if(!ParseComment(OrderGetString(ORDER_COMMENT), id, dir, level)) continue;
      if(level != 2) continue;
      if(HasOpenLevel(id, dir, 1)) continue; // L1 still alive — keep averaging limit
      if(DeleteOrder(ticket))
         PrintFormat("Basket %d: cancelled orphan L2 pending %I64u (L1 closed)", id, ticket);
   }
}

//--------------------------- MT5 Core Events ------------------------
int OnInit()
{
   fixed_mode = default_fixed_mode;
   panel_left = panel_x;
   panel_top = panel_y;

   MathSrand((uint)GetTickCount());
   trade.SetExpertMagicNumber(magic_number);
   trade.SetDeviationInPoints(deviation_points);

   handle_h1_ema = iMA(_Symbol, PERIOD_H1, h1_ema_period, 0, MODE_EMA, PRICE_CLOSE);

   ChartSetInteger(0, CHART_EVENT_MOUSE_MOVE, true);
   CreatePanel();

   PrintFormat("OLC Prop EA V2.31 | symbol=%s preset=%s level=%.5f sl=%.5f tp=%.5f risk=%.2f%%",
               _Symbol, PresetName(), PresetLevelGap(), PresetSLGap(), PresetProfitX(), PresetRiskPercent());
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(handle_h1_ema != INVALID_HANDLE) IndicatorRelease(handle_h1_ema);
   DeletePanelObjects();
   ChartSetInteger(0, CHART_EVENT_MOUSE_MOVE, false);
}

void OnTick()
{
   CheckDailyDrawdownGuard();
   ManageBasketPendings();

   if(!daily_limit_breached)
   {
      RunStrategy_NY_ORB();
      RunStrategy_AsianSweep();
   }

   int pos_count = 0; int ord_count = 0; double profit = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!IsOurPositionByIndex(i)) continue;
      pos_count++;
      profit += PositionGetDouble(POSITION_PROFIT);
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!IsOurOrderByIndex(i)) continue;
      ord_count++;
   }

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double mid = (bid + ask) / 2.0;

   SetText(stat_pos, IntegerToString(pos_count), clrWhite);
   SetText(stat_ord, IntegerToString(ord_count), clrWhite);
   SetText(stat_profit, DoubleToString(profit, 2), profit >= 0.0 ? clrLimeGreen : clrTomato);
   SetText(stat_price, DoubleToString(mid, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)), clrWhite);
   SetText(stat_spread, DoubleToString(SpreadPrice(), 2), clrDodgerBlue);

   if(daily_limit_breached)
      SetText(daily_guard_label, "Daily Guard (2%): BREACHED", clrTomato);
   else
      SetText(daily_guard_label, "Daily Guard (2%): SAFE", clrSpringGreen);
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_OBJECT_CLICK)
   {
      if(sparam == btn_buy)
      {
         StartBasket(ORDER_TYPE_BUY);
         ObjectSetInteger(0, btn_buy, OBJPROP_STATE, false);
      }
      else if(sparam == btn_sell)
      {
         StartBasket(ORDER_TYPE_SELL);
         ObjectSetInteger(0, btn_sell, OBJPROP_STATE, false);
      }
      else if(sparam == btn_close)
      {
         CloseAllOlc();
         ObjectSetInteger(0, btn_close, OBJPROP_STATE, false);
      }
      ChartRedraw();
   }
}
//+------------------------------------------------------------------+
