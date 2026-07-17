//+------------------------------------------------------------------+
//|                                             apex_mt4_bridge.mq4  |
//|                                  Copyright 2026, APEX FX Quant   |
//|                                        https://apexfx.vercel.app |
//+------------------------------------------------------------------+
//
//  v3.0 — Full Native TMS (Trade Management System)
//
//  Architecture:
//    Python Brain  → Strategy, sizing, Supabase state, time-stop & squeeze detection
//    EA  (this)    → EVERYTHING time-critical: TP1 partial, BE stop, Chandelier trail
//
//  Signal formats accepted:
//    Entry:  {"symbol":"EURUSD","cmd":"buy","volume":0.10,"sl":1.07500,"tp":1.09500,
//             "tp1":1.08500,"tp1_volume":0.05,"be_buffer":0.00030,
//             "trail_atr_mult":2.0,"trail_lookback":22}
//    Modify: {"symbol":"EURUSD","cmd":"modify_sl","ticket":12345,"new_sl":1.07800}
//    Partial:{"symbol":"EURUSD","cmd":"partial_close","ticket":12345,"volume":0.05}
//    Close:  {"symbol":"EURUSD","cmd":"close","volume":0.10,"sl":0,"tp":0}
//
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, APEX FX Quant"
#property link      "https://apexfx.vercel.app"
#property version   "3.00"
#property strict

//--- Input Parameters
input string   SignalFileName        = "mt4_signals.json";
input int      SlippagePips          = 3;
input int      MagicNumber           = 88888;
input int      ScanIntervalMs        = 200;    // 200ms for faster TP1 detection
input bool     EnableNativeTMS       = true;   // Master TMS switch
input double   DefaultTrailATRMult   = 2.0;    // Chandelier ATR multiplier
input int      DefaultTrailLookback  = 22;     // Swing high/low lookback bars
input double   DefaultBEBuffer       = 0.0003; // Breakeven buffer in price units

//--- Per-ticket TMS state storage (supports up to 50 simultaneous positions)
#define MAX_POSITIONS 50
int    tms_ticket      [MAX_POSITIONS];
double tms_tp1         [MAX_POSITIONS];  // First partial TP price
double tms_tp1_vol     [MAX_POSITIONS];  // Lots to close at TP1
double tms_be_buffer   [MAX_POSITIONS];  // Breakeven buffer
double tms_trail_mult  [MAX_POSITIONS];  // Chandelier ATR multiplier
int    tms_trail_lb    [MAX_POSITIONS];  // Chandelier lookback bars
bool   tms_tp1_hit     [MAX_POSITIONS];  // Has TP1 already fired?
bool   tms_be_set      [MAX_POSITIONS];  // Has BE stop been set?
int    tms_count       = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   Print("[APEX v3] Initializing Trade Management System Bridge...");
   ObjectCreate(0, "ApexStatus", OBJ_LABEL, 0, 0, 0);
   ObjectSetString(0, "ApexStatus", OBJPROP_TEXT, "APEX BRIDGE ACTIVE");
   ObjectSetInteger(0, "ApexStatus", OBJPROP_COLOR, clrYellow);
   ObjectSetInteger(0, "ApexStatus", OBJPROP_FONTSIZE, 20);
   ObjectSetInteger(0, "ApexStatus", OBJPROP_XDISTANCE, 50);
   ObjectSetInteger(0, "ApexStatus", OBJPROP_YDISTANCE, 50);
   ChartRedraw(0);

   ArrayInitialize(tms_ticket,    -1);
   ArrayInitialize(tms_tp1,        0.0);
   ArrayInitialize(tms_tp1_vol,    0.0);
   ArrayInitialize(tms_be_buffer,  0.0);
   ArrayInitialize(tms_trail_mult, 2.0);
   ArrayInitialize(tms_trail_lb,   22);
   ArrayInitialize(tms_tp1_hit,    false);
   ArrayInitialize(tms_be_set,     false);
   tms_count = 0;
   EventSetMillisecondTimer(ScanIntervalMs);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("[APEX v3] Deinitialized. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Main loop — runs every 200ms                                     |
//+------------------------------------------------------------------+
void OnTimer()
{
   // 1. Write position / account data for Python to read
   WriteLiveTrades();
   WriteClosedHistory();
   WriteAccountInfo();

   // 2. Run native TMS on all tracked positions (every tick matters here)
   if(EnableNativeTMS) RunNativeTMS();

   // 3. Check for new Python signal file
   if(!FileIsExist(SignalFileName, FILE_COMMON)) return;

   int handle = FileOpen(SignalFileName, FILE_READ|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("[APEX v3] Could not open signal file.");
      return;
   }
   string content = "";
   while(!FileIsEnding(handle)) content += FileReadString(handle);
   FileClose(handle);
   FileDelete(SignalFileName, FILE_COMMON);

   Print("[APEX v3] Signal: ", content);

   string cmd    = ParseJsonString(content, "cmd");
   string symbol = ParseJsonString(content, "symbol");

   if(cmd == "" || symbol == "")
   {
      Print("[APEX v3] Bad signal: missing cmd or symbol.");
      return;
   }

   if(cmd == "partial_close" || cmd == "modify_sl")
   {
      int ticket    = ParseJsonInt(content, "ticket");
      double vol    = ParseJsonDouble(content, "volume");
      double new_sl = ParseJsonDouble(content, "new_sl");
      ExecuteTMSCommand(cmd, ticket, vol, new_sl);
      return;
   }

   double volume = ParseJsonDouble(content, "volume");
   double sl     = ParseJsonDouble(content, "sl");
   double tp     = ParseJsonDouble(content, "tp");

   string matchedSym = MatchSymbol(symbol);
   if(matchedSym == "")
   {
      Print("[APEX v3] Symbol '", symbol, "' not in Market Watch.");
      return;
   }

   // Extract TMS parameters from signal
   double tp1         = ParseJsonDouble(content, "tp1");
   double tp1_vol     = ParseJsonDouble(content, "tp1_volume");
   double be_buf      = ParseJsonDouble(content, "be_buffer");
   double trail_mult  = ParseJsonDouble(content, "trail_atr_mult");
   int    trail_lb    = ParseJsonInt(content, "trail_lookback");

   if(be_buf    <= 0) be_buf    = DefaultBEBuffer;
   if(trail_mult<= 0) trail_mult= DefaultTrailATRMult;
   if(trail_lb  <= 0) trail_lb  = DefaultTrailLookback;

   ExecuteOrder(matchedSym, cmd, volume, sl, tp, tp1, tp1_vol, be_buf, trail_mult, trail_lb);
}

//+------------------------------------------------------------------+
//| Native TMS — called every 200ms for all tracked tickets          |
//+------------------------------------------------------------------+
void RunNativeTMS()
{
   // Clean up closed tickets first
   PruneTMSState();

   for(int i = 0; i < tms_count; i++)
   {
      int ticket = tms_ticket[i];
      if(ticket < 0) continue;
      if(!OrderSelect(ticket, SELECT_BY_TICKET, MODE_TRADES)) continue;

      string sym    = OrderSymbol();
      int    oType  = OrderType();
      if(oType > 1) continue; // skip pending orders

      double bid = SymbolInfoDouble(sym, SYMBOL_BID);
      double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
      int    dgt = (int)MarketInfo(sym, MODE_DIGITS);

      // ── Technique 1+2: TP1 Partial Close + Breakeven Stop ──────────────
      if(!tms_tp1_hit[i] && tms_tp1[i] > 0 && tms_tp1_vol[i] > 0)
      {
         bool tp1_triggered = false;
         if(oType == OP_BUY  && bid >= tms_tp1[i]) tp1_triggered = true;
         if(oType == OP_SELL && ask <= tms_tp1[i]) tp1_triggered = true;

         if(tp1_triggered)
         {
            double closeLots = NormalizeDouble(MathMin(tms_tp1_vol[i], OrderLots()), 2);
            double closePrice = (oType == OP_BUY) ? bid : ask;

            bool res = OrderClose(ticket, closeLots, closePrice, SlippagePips, clrYellow);
            if(res)
            {
               tms_tp1_hit[i] = true;
               Print("[APEX TMS] TP1 partial close: #", ticket, " closed ", closeLots, " lots @ ", closePrice);

               // Immediately set breakeven stop
               if(!tms_be_set[i])
               {
                  double openPrice = OrderOpenPrice();
                  double be_sl     = (oType == OP_BUY)
                                     ? NormalizeDouble(openPrice + tms_be_buffer[i], dgt)
                                     : NormalizeDouble(openPrice - tms_be_buffer[i], dgt);

                  // Validate BE stop won't trigger immediately
                  bool valid = (oType == OP_BUY && be_sl < bid) || (oType == OP_SELL && be_sl > ask);
                  if(valid)
                  {
                     bool beRes = OrderModify(ticket, openPrice, be_sl, OrderTakeProfit(), 0, clrAqua);
                     if(beRes)
                     {
                        tms_be_set[i] = true;
                        Print("[APEX TMS] Breakeven stop set: #", ticket, " SL -> ", be_sl);
                     }
                  }
               }
            }
            else
            {
               Print("[APEX TMS] TP1 partial FAILED #", ticket, " err=", GetLastError());
            }
         }
      }

      // ── Technique 3: Chandelier ATR Trail ──────────────────────────────
      // Only trail after TP1 is hit (we have a free ride now)
      if(tms_tp1_hit[i] || tms_tp1[i] <= 0)
      {
         double atr = iATR(sym, 0, 14, 1);
         if(atr <= 0) continue;

         double newSL;
         bool   doModify = false;

         if(oType == OP_BUY)
         {
            int    hiIdx = iHighest(sym, 0, MODE_HIGH, tms_trail_lb[i], 1);
            double swing = iHigh(sym, 0, hiIdx);
            newSL = NormalizeDouble(swing - (tms_trail_mult[i] * atr), dgt);
            if(newSL > OrderStopLoss() && newSL < bid) doModify = true;
         }
         else if(oType == OP_SELL)
         {
            int    loIdx = iLowest(sym, 0, MODE_LOW, tms_trail_lb[i], 1);
            double swing = iLow(sym, 0, loIdx);
            newSL = NormalizeDouble(swing + (tms_trail_mult[i] * atr), dgt);
            if(newSL < OrderStopLoss() && newSL > ask) doModify = true;
         }

         if(doModify)
         {
            bool res = OrderModify(ticket, OrderOpenPrice(), newSL, OrderTakeProfit(), 0, clrBlue);
            if(res) Print("[APEX TMS] Chandelier trail: #", ticket, " SL -> ", newSL);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Remove closed tickets from TMS state array                       |
//+------------------------------------------------------------------+
void PruneTMSState()
{
   for(int i = tms_count - 1; i >= 0; i--)
   {
      if(tms_ticket[i] < 0) continue;
      if(!OrderSelect(tms_ticket[i], SELECT_BY_TICKET, MODE_TRADES))
      {
         // Ticket no longer in open trades — remove it
         for(int j = i; j < tms_count - 1; j++)
         {
            tms_ticket[j]     = tms_ticket[j+1];
            tms_tp1[j]        = tms_tp1[j+1];
            tms_tp1_vol[j]    = tms_tp1_vol[j+1];
            tms_be_buffer[j]  = tms_be_buffer[j+1];
            tms_trail_mult[j] = tms_trail_mult[j+1];
            tms_trail_lb[j]   = tms_trail_lb[j+1];
            tms_tp1_hit[j]    = tms_tp1_hit[j+1];
            tms_be_set[j]     = tms_be_set[j+1];
         }
         tms_count--;
      }
   }
}

//+------------------------------------------------------------------+
//| Register a ticket in the TMS tracking array                      |
//+------------------------------------------------------------------+
void RegisterTMSTicket(int ticket, double tp1, double tp1_vol,
                       double be_buf, double trail_mult, int trail_lb)
{
   if(tms_count >= MAX_POSITIONS)
   {
      Print("[APEX TMS] WARNING: TMS tracking array full. Cannot track #", ticket);
      return;
   }
   tms_ticket[tms_count]     = ticket;
   tms_tp1[tms_count]        = tp1;
   tms_tp1_vol[tms_count]    = tp1_vol;
   tms_be_buffer[tms_count]  = be_buf;
   tms_trail_mult[tms_count] = trail_mult;
   tms_trail_lb[tms_count]   = trail_lb;
   tms_tp1_hit[tms_count]    = false;
   tms_be_set[tms_count]     = false;
   tms_count++;
   Print("[APEX TMS] Registered ticket #", ticket,
         " TP1=", tp1, " Vol=", tp1_vol,
         " Trail=", trail_mult, "xATR lb=", trail_lb);
}

//+------------------------------------------------------------------+
//| Execute TMS commands from Python (modify_sl / partial_close)     |
//+------------------------------------------------------------------+
void ExecuteTMSCommand(string cmd, int ticket, double volume, double new_sl)
{
   if(!OrderSelect(ticket, SELECT_BY_TICKET, MODE_TRADES))
   {
      if(OrderSelect(ticket, SELECT_BY_TICKET, MODE_HISTORY))
         Print("[APEX TMS] Ticket #", ticket, " already closed.");
      else
         Print("[APEX TMS] Ticket #", ticket, " not found. Err=", GetLastError());
      return;
   }

   string sym = OrderSymbol();
   int    dgt = (int)MarketInfo(sym, MODE_DIGITS);
   RefreshRates();

   if(cmd == "partial_close")
   {
      int    oType      = OrderType();
      double closePrice = (oType == OP_BUY) ? SymbolInfoDouble(sym, SYMBOL_BID)
                                            : SymbolInfoDouble(sym, SYMBOL_ASK);
      double closeLots  = NormalizeDouble(MathMin(volume, OrderLots()), 2);
      if(closeLots < 0.01) { Print("[APEX TMS] partial_close: invalid volume."); return; }

      bool res = OrderClose(ticket, closeLots, closePrice, SlippagePips, clrYellow);
      if(res) Print("[APEX TMS] Partial close OK: #", ticket, " ", closeLots, " lots @ ", closePrice);
      else    Print("[APEX TMS] Partial close FAILED #", ticket, " err=", GetLastError());
   }
   else if(cmd == "modify_sl")
   {
      int    oType = OrderType();
      double bid   = SymbolInfoDouble(sym, SYMBOL_BID);
      double ask   = SymbolInfoDouble(sym, SYMBOL_ASK);

      new_sl = NormalizeDouble(new_sl, dgt);
      if(oType == OP_BUY  && new_sl >= bid) { Print("[APEX TMS] modify_sl skip: sl >= bid"); return; }
      if(oType == OP_SELL && new_sl <= ask) { Print("[APEX TMS] modify_sl skip: sl <= ask"); return; }

      bool res = OrderModify(ticket, OrderOpenPrice(), new_sl, OrderTakeProfit(), 0, clrAqua);
      if(res) Print("[APEX TMS] SL modified: #", ticket, " -> ", new_sl);
      else    Print("[APEX TMS] SL modify FAILED #", ticket, " err=", GetLastError());

      // Also update our TMS array for consistent trailing reference
      for(int i = 0; i < tms_count; i++)
         if(tms_ticket[i] == ticket)
            break; // SL is already applied to the order; Chandelier trail picks it up from OrderStopLoss()
   }
}

//+------------------------------------------------------------------+
//| Execute entry / close order + register with TMS                 |
//+------------------------------------------------------------------+
void ExecuteOrder(string sym, string cmd, double vol, double sl, double tp,
                  double tp1, double tp1_vol, double be_buf,
                  double trail_mult, int trail_lb)
{
   if(cmd == "close")
   {
      RefreshRates();
      for(int i = OrdersTotal() - 1; i >= 0; i--)
      {
         if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
         if(OrderSymbol() != sym || OrderMagicNumber() != MagicNumber) continue;
         int    oType      = OrderType();
         double closePrice = (oType == OP_BUY) ? SymbolInfoDouble(sym, SYMBOL_BID)
                                               : SymbolInfoDouble(sym, SYMBOL_ASK);
         bool res = OrderClose(OrderTicket(), OrderLots(), closePrice, SlippagePips, clrOrange);
         if(res) Print("[APEX v3] Closed #", OrderTicket());
         else    Print("[APEX v3] Close FAILED #", OrderTicket(), " err=", GetLastError());
      }
      return;
   }

   int    type;
   double price;
   color  arrow;

   if     (cmd == "buy")  { type = OP_BUY;  price = SymbolInfoDouble(sym, SYMBOL_ASK); arrow = clrGreen; }
   else if(cmd == "sell") { type = OP_SELL; price = SymbolInfoDouble(sym, SYMBOL_BID); arrow = clrRed;   }
   else   { Print("[APEX v3] Unknown cmd: ", cmd); return; }

   RefreshRates();
   Print("[APEX v3] Order: ", cmd, " ", vol, " lots ", sym, " @ ", price, " SL:", sl, " TP:", tp,
         " | TP1:", tp1, " Vol:", tp1_vol);

   int ticket = OrderSend(sym, type, vol, price, SlippagePips, sl, tp,
                          "APEX TMS v3", MagicNumber, 0, arrow);

   if(ticket > 0)
   {
      Print("[APEX v3] Order #", ticket, " opened on ", sym);
      // Register with native TMS for TP1, BE, and Chandelier trail
      if(EnableNativeTMS)
         RegisterTMSTicket(ticket, tp1, tp1_vol, be_buf, trail_mult, trail_lb);
   }
   else
   {
      Print("[APEX v3] Order FAILED ", sym, " err=", GetLastError(), " - ", ErrorDescription(GetLastError()));
   }
}

//+------------------------------------------------------------------+
//| Symbol matching                                                   |
//+------------------------------------------------------------------+
string CleanSymbolName(string text)
{
   string r = "";
   for(int j = 0; j < StringLen(text); j++)
   {
      ushort c = StringGetCharacter(text, j);
      if((c>=65&&c<=90)||(c>=97&&c<=122)||(c>=48&&c<=57))
      {
         string ch=" "; StringSetCharacter(ch,0,c); r+=ch;
      }
   }
   StringToUpper(r); return r;
}
string MatchSymbol(string base)
{
   string cb = CleanSymbolName(base);
   for(int k=0;k<SymbolsTotal(false);k++)
   {
      string s=SymbolName(k,false);
      if(StringFind(CleanSymbolName(s),cb)>=0) return s;
   }
   return "";
}

//+------------------------------------------------------------------+
//| JSON Parsers                                                      |
//+------------------------------------------------------------------+
string ParseJsonString(string json, string key)
{
   string sk="\""+key+"\"";
   int idx=StringFind(json,sk); if(idx<0) return "";
   int col=StringFind(json,":",idx); if(col<0) return "";
   int q1=StringFind(json,"\"",col); if(q1<0) return "";
   int q2=StringFind(json,"\"",q1+1); if(q2<0) return "";
   return StringSubstr(json,q1+1,q2-q1-1);
}
double ParseJsonDouble(string json, string key)
{
   string sk="\""+key+"\"";
   int idx=StringFind(json,sk); if(idx<0) return 0.0;
   int col=StringFind(json,":",idx); if(col<0) return 0.0;
   int ci=StringFind(json,",",col), bi=StringFind(json,"}",col);
   int end=(ci>0&&ci<bi)?ci:bi; if(end<0) return 0.0;
   string v=StringTrim(StringSubstr(json,col+1,end-col-1));
   v=ApexReplace(v,"\"",""); return StringToDouble(v);
}
int ParseJsonInt(string json, string key)
{
   return (int)MathRound(ParseJsonDouble(json,key));
}
string StringTrim(string t)
{
   int s=0,e=StringLen(t)-1;
   while(s<=e&&(StringGetCharacter(t,s)==' '||StringGetCharacter(t,s)=='\t'||StringGetCharacter(t,s)=='\n'||StringGetCharacter(t,s)=='\r'))s++;
   while(e>=s&&(StringGetCharacter(t,e)==' '||StringGetCharacter(t,e)=='\t'||StringGetCharacter(t,e)=='\n'||StringGetCharacter(t,e)=='\r'))e--;
   return StringSubstr(t,s,e-s+1);
}
string ApexReplace(string text,string tgt,string rep)
{
   string r=text; int i=StringFind(r,tgt);
   while(i>=0){r=StringSubstr(r,0,i)+rep+StringSubstr(r,i+StringLen(tgt));i=StringFind(r,tgt,i+StringLen(rep));}
   return r;
}
string ErrorDescription(int e)
{
   switch(e)
   {
      case 3: return "Invalid params"; case 4: return "Server busy";
      case 129:return "Invalid price"; case 130:return "Invalid stops";
      case 131:return "Invalid volume";case 132:return "Market closed";
      case 133:return "Trade disabled";case 134:return "Not enough money";
      case 135:return "Price changed"; case 138:return "Requote";
      case 146:return "Context busy";  default: return "Err "+IntegerToString(e);
   }
}

//+------------------------------------------------------------------+
//| Write live positions JSON                                         |
//+------------------------------------------------------------------+
void WriteLiveTrades()
{
   int h=FileOpen("mt4_positions.json",FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h==INVALID_HANDLE) return;
   string c="["; bool first=true;
   for(int i=0;i<OrdersTotal();i++)
   {
      if(!OrderSelect(i,SELECT_BY_POS,MODE_TRADES)) continue;
      if(OrderType()>1) continue;
      if(!first) c+=","; first=false;
      c+="{\"ticket\":"+IntegerToString(OrderTicket())+
         ",\"symbol\":\""+OrderSymbol()+"\""+
         ",\"cmd\":"+IntegerToString(OrderType())+
         ",\"volume\":"+DoubleToString(OrderLots(),2)+
         ",\"open_price\":"+DoubleToString(OrderOpenPrice(),5)+
         ",\"sl\":"+DoubleToString(OrderStopLoss(),5)+
         ",\"tp\":"+DoubleToString(OrderTakeProfit(),5)+
         ",\"close_price\":"+DoubleToString(MarketInfo(OrderSymbol(),MODE_BID),5)+
         ",\"profit\":"+DoubleToString(OrderProfit()+OrderSwap()+OrderCommission(),2)+
         ",\"magic\":"+IntegerToString(OrderMagicNumber())+
         ",\"open_time\":"+IntegerToString(OrderOpenTime())+"}";
   }
   c+="]"; FileWriteString(h,c); FileClose(h);
}
void WriteClosedHistory()
{
   int h=FileOpen("mt4_history.json",FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h==INVALID_HANDLE) return;
   string c="["; bool first=true; int cnt=0;
   for(int i=OrdersHistoryTotal()-1;i>=0&&cnt<50;i--)
   {
      if(!OrderSelect(i,SELECT_BY_POS,MODE_HISTORY)) continue;
      if(OrderType()>1) continue;
      if(!first) c+=","; first=false; cnt++;
      c+="{\"ticket\":"+IntegerToString(OrderTicket())+
         ",\"symbol\":\""+OrderSymbol()+"\""+
         ",\"cmd\":"+IntegerToString(OrderType())+
         ",\"volume\":"+DoubleToString(OrderLots(),2)+
         ",\"open_price\":"+DoubleToString(OrderOpenPrice(),5)+
         ",\"sl\":"+DoubleToString(OrderStopLoss(),5)+
         ",\"tp\":"+DoubleToString(OrderTakeProfit(),5)+
         ",\"close_price\":"+DoubleToString(OrderClosePrice(),5)+
         ",\"profit\":"+DoubleToString(OrderProfit()+OrderSwap()+OrderCommission(),2)+
         ",\"magic\":"+IntegerToString(OrderMagicNumber())+
         ",\"open_time\":"+IntegerToString(OrderOpenTime())+
         ",\"close_time\":"+IntegerToString(OrderCloseTime())+"}";
   }
   c+="]"; FileWriteString(h,c); FileClose(h);
}
void WriteAccountInfo()
{
   int h=FileOpen("mt4_account.json",FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h==INVALID_HANDLE) return;
   double sb=0.0;
   for(int i=0;i<OrdersHistoryTotal();i++)
      if(OrderSelect(i,SELECT_BY_POS,MODE_HISTORY)&&OrderType()==6&&OrderProfit()>0) sb+=OrderProfit();
   if(sb<=0.0) sb=AccountBalance()-AccountProfit();
   string c="{\"balance\":"+DoubleToString(AccountBalance(),2)+
            ",\"equity\":"+DoubleToString(AccountEquity(),2)+
            ",\"profit\":"+DoubleToString(AccountProfit(),2)+
            ",\"free_margin\":"+DoubleToString(AccountFreeMargin(),2)+
            ",\"leverage\":"+IntegerToString(AccountLeverage())+
            ",\"currency\":\""+AccountCurrency()+"\""+
            ",\"name\":\""+AccountName()+"\""+
            ",\"company\":\""+AccountCompany()+"\""+
            ",\"start_balance\":"+DoubleToString(sb,2)+"}";
   FileWriteString(h,c); FileClose(h);
}
