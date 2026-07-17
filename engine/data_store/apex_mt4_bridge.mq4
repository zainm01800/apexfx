//+------------------------------------------------------------------+
//|                                             apex_mt4_bridge.mq4  |
//|                                  Copyright 2026, APEX FX Quant   |
//|                                        https://apexfx.vercel.app |
//+------------------------------------------------------------------+
//| v1.10 (2026-07-17) — fills handshake + ticket-scoped ops          |
//|                                                                     |
//| Supersedes BOTH the repo v1.00 and the terminal v3.00:              |
//|  * Batch-processes unique per-signal files signal_*.json (the old  |
//|    single-slot mt4_signals.json was last-write-wins and dropped    |
//|    orders written inside one poll window). The legacy single-slot  |
//|    file is still consumed for backward compatibility.              |
//|  * Ticket-scoped close / partial_close / modify_sl: when the       |
//|    signal carries a "ticket" field, ONLY that ticket is touched    |
//|    (a symbol-scoped close used to flatten every engine position    |
//|    on the pair, across all timeframes).                            |
//|  * Writes a fill receipt ack_<id>.json after executing any order:  |
//|    client order id + MT4 ticket + fill price. Python never marks   |
//|    a trade filled without this ack.                                |
//|  * mt4_account.json now carries "server_time" (TimeCurrent) so     |
//|    the engine derives the broker UTC offset live (DST-safe).       |
//|  * Keeps the v3.00 native TMS: TP1 partial close, breakeven stop   |
//|    and Chandelier ATR trail run on the terminal every poll.        |
//|                                                                     |
//|  Signal formats accepted (signal_<id>.json, or legacy file):       |
//|    Entry:  {"id":"..","symbol":"EURUSD","cmd":"buy","volume":0.10, |
//|             "sl":1.07500,"tp":1.09500,"tp1":1.08500,               |
//|             "tp1_volume":0.05,"be_buffer":0.00030,                 |
//|             "trail_atr_mult":2.0,"trail_lookback":22}              |
//|    Modify: {"id":"..","symbol":"EURUSD","cmd":"modify_sl",         |
//|             "ticket":12345,"new_sl":1.07800}                       |
//|    Partial:{"id":"..","symbol":"EURUSD","cmd":"partial_close",     |
//|             "ticket":12345,"volume":0.05}                          |
//|    Close:  {"id":"..","symbol":"EURUSD","cmd":"close",             |
//|             "ticket":12345}  (no ticket = legacy close-all-on-pair)|
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, APEX FX Quant"
#property link      "https://apexfx.vercel.app"
#property version   "1.10"
#property strict

//--- Input Parameters
input string   SignalFileName        = "mt4_signals.json"; // Legacy single-slot signal file (engine < 2026-07-17)
input string   SignalGlob            = "signal_*.json";    // v1.10: unique per-signal files, batch-processed
input int      SlippagePips          = 3;                  // Allowed slippage in pips
input int      MagicNumber           = 88888;              // Unique ID for APEX trades
input int      ScanIntervalMs        = 500;                // Scan speed in milliseconds
input bool     EnableNativeTMS       = true;               // Native TP1/BE/Chandelier management
input double   DefaultTrailATRMult   = 2.0;                // Chandelier ATR multiplier
input int      DefaultTrailLookback  = 22;                 // Swing high/low lookback bars
input double   DefaultBEBuffer       = 0.0003;             // Breakeven buffer in price units

//--- Per-ticket native TMS state (supports up to 50 simultaneous positions)
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
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("[APEX Bridge] Initializing MT4 Signal Bridge v1.10 (fills handshake, ticket-scoped ops, native TMS)...");
   ObjectCreate(0, "ApexStatus", OBJ_LABEL, 0, 0, 0);
   ObjectSetString(0, "ApexStatus", OBJPROP_TEXT, "APEX BRIDGE v1.10 ACTIVE");
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

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("[APEX Bridge] Deinitialized.");
}

//+------------------------------------------------------------------+
//| Timer event function (scans for new JSON signals)                 |
//+------------------------------------------------------------------+
void OnTimer()
{
   // 1. Write position / account data for Python to read
   WriteLiveTrades();
   WriteClosedHistory();
   WriteAccountInfo();

   // 2. Run native TMS on all tracked positions (every poll)
   if(EnableNativeTMS) RunNativeTMS();

   // 3. Legacy single-slot file (engine older than the fills handshake)
   if(FileIsExist(SignalFileName, FILE_COMMON))
   {
      ProcessSignalFile(SignalFileName);
   }

   // 4. v1.10: batch-process every pending unique signal file
   int search = FileFindFirst(SignalGlob, FILE_COMMON);
   if(search == INVALID_HANDLE)
   {
      return; // No pending signals
   }
   do
   {
      string fname = FileFindFileName(search);
      if(StringLen(fname) > 0)
      {
         ProcessSignalFile(fname);
      }
   }
   while(FileFindNext(search));
   FileFindClose(search);
}

//+------------------------------------------------------------------+
//| Read, consume and execute one signal file (defensive: any parse   |
//| error skips the file — it is deleted so it cannot execute twice)  |
//+------------------------------------------------------------------+
void ProcessSignalFile(string fname)
{
   int handle = FileOpen(fname, FILE_READ|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("[APEX Bridge] Error: Found signal file '", fname, "' but could not open it.");
      return;
   }

   string content = "";
   while(!FileIsEnding(handle))
   {
      content += FileReadString(handle);
   }
   FileClose(handle);

   // Consume exactly once — delete before executing to avoid double-execution.
   FileDelete(fname, FILE_COMMON);

   Print("[APEX Bridge] Received signal (", fname, "): ", content);

   // Parse fields manually from simple JSON payload
   string id     = ParseJsonString(content, "id");
   string symbol = ParseJsonString(content, "symbol");
   string cmd    = ParseJsonString(content, "cmd");
   double volume = ParseJsonDouble(content, "volume");
   double sl     = ParseJsonDouble(content, "sl");
   double tp     = ParseJsonDouble(content, "tp");
   double newSl  = ParseJsonDouble(content, "new_sl");
   long   ticket = (long)ParseJsonDouble(content, "ticket");

   if(symbol == "" || cmd == "")
   {
      Print("[APEX Bridge] Error: Failed to parse execution parameters in '", fname, "' — skipped.");
      if(id != "") WriteAck(id, cmd, symbol, 0, 0.0, 0.0, false, "parse_error");
      return;
   }

   // Native TMS parameters (entries only)
   double tp1        = ParseJsonDouble(content, "tp1");
   double tp1_vol    = ParseJsonDouble(content, "tp1_volume");
   double be_buf     = ParseJsonDouble(content, "be_buffer");
   double trail_mult = ParseJsonDouble(content, "trail_atr_mult");
   int    trail_lb   = ParseJsonInt(content, "trail_lookback");
   if(be_buf     <= 0) be_buf     = DefaultBEBuffer;
   if(trail_mult <= 0) trail_mult = DefaultTrailATRMult;
   if(trail_lb   <= 0) trail_lb   = DefaultTrailLookback;

   // Standardise Symbol Name (handle broker suffix like EURUSDm or EURUSD.ecn).
   // Ticket-scoped commands do not need a Market Watch match — the ticket
   // carries its own symbol — so only entries and legacy closes require it.
   string matchedSymbol = MatchSymbol(symbol);

   bool   ok = false;
   int    resultTicket = 0;
   double fillPrice = 0.0;
   string err = "";

   if(cmd == "buy" || cmd == "sell")
   {
      if(matchedSymbol == "")      err = "symbol_not_found";
      else if(volume <= 0.0)       err = "invalid_volume";
      else ok = ExecuteEntry(matchedSymbol, cmd, volume, sl, tp,
                             tp1, tp1_vol, be_buf, trail_mult, trail_lb,
                             resultTicket, fillPrice, err);
   }
   else if(cmd == "close")
   {
      if(ticket > 0)
      {
         // Ticket-scoped: close ONLY this ticket, full volume.
         ok = CloseTicket((int)ticket, 0.0, fillPrice, err);
         resultTicket = (int)ticket;
      }
      else
      {
         // Legacy symbol-scoped semantics (pre-handshake trades only):
         // close ALL engine positions on the pair.
         if(matchedSymbol == "") err = "symbol_not_found";
         else ok = (CloseAllBySymbol(matchedSymbol) > 0);
         if(!ok && err == "") err = "no_position_closed";
      }
   }
   else if(cmd == "partial_close")
   {
      if(ticket <= 0)       err = "ticket_required";
      else if(volume <= 0)  err = "invalid_volume";
      else
      {
         ok = CloseTicket((int)ticket, volume, fillPrice, err);
         resultTicket = (int)ticket;
      }
   }
   else if(cmd == "modify_sl")
   {
      if(ticket <= 0)      err = "ticket_required";
      else if(newSl <= 0)  err = "invalid_new_sl";
      else
      {
         ok = ModifyTicketSl((int)ticket, newSl, err);
         resultTicket = (int)ticket;
      }
   }
   else
   {
      err = "invalid_cmd";
      Print("[APEX Bridge] Invalid order command: ", cmd);
   }

   if(err != "")
   {
      Print("[APEX Bridge] Signal '", fname, "' failed: ", err);
   }
   WriteAck(id, cmd, symbol, resultTicket, fillPrice, volume, ok, err);
}

//+------------------------------------------------------------------+
//| Write the fill receipt ack_<id>.json for the fills handshake      |
//+------------------------------------------------------------------+
void WriteAck(string id, string cmd, string symbol, int ticket, double fillPrice,
              double volume, bool ok, string err)
{
   if(id == "") return; // legacy signals carry no client order id

   string fname = "ack_" + id + ".json";
   int handle = FileOpen(fname, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("[APEX Bridge] Error: could not write ack file '", fname, "'.");
      return;
   }

   string content = "{\"id\":\"" + id + "\"" +
                    ",\"cmd\":\"" + cmd + "\"" +
                    ",\"symbol\":\"" + symbol + "\"" +
                    ",\"ticket\":" + IntegerToString(ticket) +
                    ",\"fill_price\":" + DoubleToString(fillPrice, 5) +
                    ",\"volume\":" + DoubleToString(volume, 2) +
                    ",\"ok\":" + (ok ? "true" : "false") +
                    ",\"error\":\"" + err + "\"" +
                    ",\"server_time\":" + IntegerToString((int)TimeCurrent()) + "}";
   FileWriteString(handle, content);
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Execute a market entry; registers the ticket with native TMS      |
//+------------------------------------------------------------------+
bool ExecuteEntry(string sym, string cmd, double vol, double sl, double tp,
                  double tp1, double tp1_vol, double be_buf,
                  double trail_mult, int trail_lb,
                  int &resultTicket, double &fillPrice, string &err)
{
   int type = (cmd == "buy") ? OP_BUY : OP_SELL;
   color arrowColor = (cmd == "buy") ? clrGreen : clrRed;

   RefreshRates();
   double price = (type == OP_BUY) ? SymbolInfoDouble(sym, SYMBOL_ASK)
                                   : SymbolInfoDouble(sym, SYMBOL_BID);

   Print("[APEX Bridge] Sending order: ", cmd, " ", vol, " lots of ", sym, " at ", price,
         " SL:", sl, " TP:", tp, " | TP1:", tp1, " Vol:", tp1_vol);

   int ticket = OrderSend(sym, type, vol, price, SlippagePips, sl, tp,
                          "APEX Quant Auto-Trade", MagicNumber, 0, arrowColor);

   if(ticket > 0)
   {
      Print("[APEX Bridge] Success! Order #", ticket, " executed on ", sym);
      resultTicket = ticket;
      fillPrice = price;
      if(OrderSelect(ticket, SELECT_BY_TICKET)) fillPrice = OrderOpenPrice();
      // Register with native TMS for TP1, BE, and Chandelier trail
      if(EnableNativeTMS)
         RegisterTMSTicket(ticket, tp1, tp1_vol, be_buf, trail_mult, trail_lb);
      return true;
   }

   int error = GetLastError();
   err = "ordersend_" + IntegerToString(error);
   Print("[APEX Bridge] Order failed on ", sym, ". Error code: ", error, " - ", ErrorDescription(error));
   return false;
}

//+------------------------------------------------------------------+
//| Close one specific ticket (full when lots<=0, else partial).      |
//| Only engine orders (MagicNumber) that are still open are touched. |
//+------------------------------------------------------------------+
bool CloseTicket(int ticket, double lots, double &fillPrice, string &err)
{
   if(!OrderSelect(ticket, SELECT_BY_TICKET, MODE_TRADES))
   {
      err = "ticket_not_found";
      Print("[APEX Bridge] Close failed: ticket #", ticket, " not found.");
      return false;
   }
   if(OrderCloseTime() != 0)
   {
      err = "ticket_already_closed";
      return false;
   }
   if(OrderMagicNumber() != MagicNumber)
   {
      err = "ticket_not_engine_owned";
      Print("[APEX Bridge] Refusing to close #", ticket, " — magic ", OrderMagicNumber(), " is not ours.");
      return false;
   }

   string sym = OrderSymbol();
   double closeLots = (lots > 0.0) ? MathMin(NormalizeDouble(lots, 2), OrderLots()) : OrderLots();
   double minLot = MarketInfo(sym, MODE_MINLOT);
   if(closeLots < minLot)
   {
      err = "volume_below_minlot";
      return false;
   }

   RefreshRates();
   double closePrice = (OrderType() == OP_BUY) ? MarketInfo(sym, MODE_BID) : MarketInfo(sym, MODE_ASK);
   bool res = OrderClose(ticket, closeLots, closePrice, SlippagePips, clrOrange);
   if(res)
   {
      fillPrice = closePrice;
      Print("[APEX Bridge] Closed ", closeLots, " lots of order #", ticket);
      return true;
   }
   err = "orderclose_" + IntegerToString(GetLastError());
   Print("[APEX Bridge] Close failed for #", ticket, ". Error: ", GetLastError());
   return false;
}

//+------------------------------------------------------------------+
//| Move the stop-loss of one specific ticket. The new SL is sanity-  |
//| checked so it cannot immediately trigger before OrderModify.      |
//+------------------------------------------------------------------+
bool ModifyTicketSl(int ticket, double newSl, string &err)
{
   if(!OrderSelect(ticket, SELECT_BY_TICKET, MODE_TRADES))
   {
      err = "ticket_not_found";
      Print("[APEX Bridge] modify_sl failed: ticket #", ticket, " not found.");
      return false;
   }
   if(OrderCloseTime() != 0)
   {
      err = "ticket_already_closed";
      return false;
   }
   if(OrderMagicNumber() != MagicNumber)
   {
      err = "ticket_not_engine_owned";
      Print("[APEX Bridge] Refusing to modify #", ticket, " — magic ", OrderMagicNumber(), " is not ours.");
      return false;
   }

   string sym = OrderSymbol();
   int dgt = (int)MarketInfo(sym, MODE_DIGITS);
   RefreshRates();
   double bid = MarketInfo(sym, MODE_BID);
   double ask = MarketInfo(sym, MODE_ASK);
   newSl = NormalizeDouble(newSl, dgt);
   // A stop must sit BELOW price for buys and ABOVE price for sells —
   // otherwise it triggers immediately.
   bool sane = (OrderType() == OP_BUY  && newSl < bid) ||
               (OrderType() == OP_SELL && newSl > ask);
   if(!sane)
   {
      err = "new_sl_would_trigger";
      Print("[APEX Bridge] modify_sl #", ticket, " rejected: new_sl ", newSl, " would trigger immediately.");
      return false;
   }

   bool res = OrderModify(ticket, OrderOpenPrice(), newSl, OrderTakeProfit(), 0, clrAqua);
   if(res)
   {
      Print("[APEX Bridge] SL of #", ticket, " moved to ", newSl);
      return true;
   }
   err = "ordermodify_" + IntegerToString(GetLastError());
   Print("[APEX Bridge] modify_sl failed for #", ticket, ". Error: ", GetLastError());
   return false;
}

//+------------------------------------------------------------------+
//| Legacy symbol-scoped close: ALL engine positions on the pair      |
//+------------------------------------------------------------------+
int CloseAllBySymbol(string sym)
{
   int closed = 0;
   RefreshRates();
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderSymbol() == sym && OrderMagicNumber() == MagicNumber)
         {
            int oType = OrderType();
            double closePrice = (oType == OP_BUY) ? SymbolInfoDouble(sym, SYMBOL_BID) : SymbolInfoDouble(sym, SYMBOL_ASK);
            bool res = OrderClose(OrderTicket(), OrderLots(), closePrice, SlippagePips, clrOrange);
            if(res)
            {
               closed++;
               Print("[APEX Bridge] Closed order #", OrderTicket());
            }
            else
            {
               Print("[APEX Bridge] Close failed for #", OrderTicket(), ". Error: ", GetLastError());
            }
         }
      }
   }
   return closed;
}

//+------------------------------------------------------------------+
//| Native TMS — called every poll for all tracked tickets            |
//| (ported from the v3.00 terminal bridge: TP1 partial + BE + trail) |
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

      // ── TP1 Partial Close + Breakeven Stop ──────────────────────────
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

      // ── Chandelier ATR Trail (only after TP1 / when no TP1 set) ─────
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
//| Clean symbol name by removing non-alphanumeric characters       |
//+------------------------------------------------------------------+
string CleanSymbolName(string text)
{
   string result = "";
   for(int j = 0; j < StringLen(text); j++)
     {
      ushort charCode = StringGetCharacter(text, j);
      if((charCode >= 65 && charCode <= 90) ||
         (charCode >= 97 && charCode <= 122) ||
         (charCode >= 48 && charCode <= 57))
        {
         string ch = " ";
         StringSetCharacter(ch, 0, charCode);
         result += ch;
        }
     }
   string upperRes = result;
   StringToUpper(upperRes);
   return upperRes;
}

//+------------------------------------------------------------------+
//| Match symbol suffix (e.g. EURUSD -> EURUSD.m)                     |
//+------------------------------------------------------------------+
string MatchSymbol(string baseSymbol)
{
   string cleanBase = CleanSymbolName(baseSymbol);
   for(int k = 0; k < SymbolsTotal(false); k++)
   {
      string sym = SymbolName(k, false);
      string cleanSym = CleanSymbolName(sym);
      if(StringFind(cleanSym, cleanBase) >= 0)
      {
         return sym;
      }
   }
   return "";
}

//+------------------------------------------------------------------+
//| Simple JSON String Extractor                                      |
//+------------------------------------------------------------------+
string ParseJsonString(string json, string key)
{
   string searchKey = "\"" + key + "\"";
   int startIdx = StringFind(json, searchKey);
   if(startIdx < 0) return "";

   int colonIdx = StringFind(json, ":", startIdx);
   if(colonIdx < 0) return "";

   int firstQuote = StringFind(json, "\"", colonIdx);
   if(firstQuote < 0) return "";

   int secondQuote = StringFind(json, "\"", firstQuote + 1);
   if(secondQuote < 0) return "";

   return StringSubstr(json, firstQuote + 1, secondQuote - firstQuote - 1);
}

//+------------------------------------------------------------------+
//| Simple JSON Double Extractor                                      |
//+------------------------------------------------------------------+
double ParseJsonDouble(string json, string key)
{
   string searchKey = "\"" + key + "\"";
   int startIdx = StringFind(json, searchKey);
   if(startIdx < 0) return 0.0;

   int colonIdx = StringFind(json, ":", startIdx);
   if(colonIdx < 0) return 0.0;

   // Read until comma or closing brace
   int commaIdx = StringFind(json, ",", colonIdx);
   int braceIdx = StringFind(json, "}", colonIdx);
   int endIdx = (commaIdx < braceIdx && commaIdx > 0) ? commaIdx : braceIdx;
   if(endIdx < 0) return 0.0;

   string valStr = StringSubstr(json, colonIdx + 1, endIdx - colonIdx - 1);
   valStr = StringTrim(valStr);

   // Clean extra quotes if type is string in JSON
   valStr = ApexStringReplace(valStr, "\"", "");

   return StringToDouble(valStr);
}

//+------------------------------------------------------------------+
//| Simple JSON Int Extractor                                         |
//+------------------------------------------------------------------+
int ParseJsonInt(string json, string key)
{
   return (int)MathRound(ParseJsonDouble(json, key));
}

//+------------------------------------------------------------------+
//| Helper to trim whitespace                                         |
//+------------------------------------------------------------------+
string StringTrim(string text)
{
   int start = 0;
   int end = StringLen(text) - 1;
   while(start <= end && (StringGetCharacter(text, start) == ' ' || StringGetCharacter(text, start) == '\t' || StringGetCharacter(text, start) == '\n' || StringGetCharacter(text, start) == '\r')) start++;
   while(end >= start && (StringGetCharacter(text, end) == ' ' || StringGetCharacter(text, end) == '\t' || StringGetCharacter(text, end) == '\n' || StringGetCharacter(text, end) == '\r')) end--;
   return StringSubstr(text, start, end - start + 1);
}

//+------------------------------------------------------------------+
//| Helper to replace characters                                      |
//+------------------------------------------------------------------+
string ApexStringReplace(string text, string target, string replacement)
{
   string result = text;
   int idx = StringFind(result, target);
   while(idx >= 0)
   {
      result = StringSubstr(result, 0, idx) + replacement + StringSubstr(result, idx + StringLen(target));
      idx = StringFind(result, target, idx + StringLen(replacement));
   }
   return result;
}

//+------------------------------------------------------------------+
//| MQL4 standard error descriptions                                  |
//+------------------------------------------------------------------+
string ErrorDescription(int errorCode)
{
   switch(errorCode)
   {
      case 1:   return "No error, but progress is unclear";
      case 2:   return "Common error";
      case 3:   return "Invalid trade parameters";
      case 4:   return "Trade server is busy";
      case 129: return "Invalid price";
      case 130: return "Invalid stops (SL or TP too close)";
      case 131: return "Invalid trade volume (lot size wrong)";
      case 132: return "Market is closed";
      case 133: return "Trade is disabled";
      case 134: return "Not enough money to open trade";
      case 135: return "Price changed";
      case 138: return "Requote";
      case 146: return "Trade context is busy";
      default:  return "Unknown error code";
   }
}

//+------------------------------------------------------------------+
//| Write live open positions to a shared common JSON file           |
//+------------------------------------------------------------------+
void WriteLiveTrades()
{
   string filename = "mt4_positions.json";
   int handle = FileOpen(filename, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE) return;

   string content = "[";
   bool first = true;
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         // Exclude balance deposits/withdrawals and pending orders (OP_BUY=0, OP_SELL=1)
         if(OrderType() > 1) continue;

         if(!first) content += ",";
         first = false;
         content += "{\"ticket\":" + IntegerToString(OrderTicket()) +
                    ",\"symbol\":\"" + OrderSymbol() + "\"" +
                    ",\"cmd\":" + IntegerToString(OrderType()) +
                    ",\"volume\":" + DoubleToString(OrderLots(), 2) +
                    ",\"open_price\":" + DoubleToString(OrderOpenPrice(), 5) +
                    ",\"sl\":" + DoubleToString(OrderStopLoss(), 5) +
                    ",\"tp\":" + DoubleToString(OrderTakeProfit(), 5) +
                    ",\"close_price\":" + DoubleToString(MarketInfo(OrderSymbol(), MODE_BID), 5) +
                    ",\"profit\":" + DoubleToString(OrderProfit() + OrderSwap() + OrderCommission(), 2) +
                    ",\"magic\":" + IntegerToString(OrderMagicNumber()) +
                    ",\"open_time\":" + IntegerToString(OrderOpenTime()) + "}";
      }
   }
   content += "]";
   FileWriteString(handle, content);
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Write closed trade history to a shared common JSON file          |
//+------------------------------------------------------------------+
void WriteClosedHistory()
{
   string filename = "mt4_history.json";
   int handle = FileOpen(filename, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE) return;

   string content = "[";
   bool first = true;
   int count = 0;
   for(int i = OrdersHistoryTotal() - 1; i >= 0 && count < 50; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
      {
         // Exclude balance transactions (OrderType > OP_SELL)
         if(OrderType() > 1) continue;

         if(!first) content += ",";
         first = false;
         count++;

         content += "{\"ticket\":" + IntegerToString(OrderTicket()) +
                    ",\"symbol\":\"" + OrderSymbol() + "\"" +
                    ",\"cmd\":" + IntegerToString(OrderType()) +
                    ",\"volume\":" + DoubleToString(OrderLots(), 2) +
                    ",\"open_price\":" + DoubleToString(OrderOpenPrice(), 5) +
                    ",\"sl\":" + DoubleToString(OrderStopLoss(), 5) +
                    ",\"tp\":" + DoubleToString(OrderTakeProfit(), 5) +
                    ",\"close_price\":" + DoubleToString(OrderClosePrice(), 5) +
                    ",\"profit\":" + DoubleToString(OrderProfit() + OrderSwap() + OrderCommission(), 2) +
                    ",\"magic\":" + IntegerToString(OrderMagicNumber()) +
                    ",\"open_time\":" + IntegerToString(OrderOpenTime()) +
                    ",\"close_time\":" + IntegerToString(OrderCloseTime()) + "}";
      }
   }
   content += "]";
   FileWriteString(handle, content);
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Write live account statistics to a shared common JSON file       |
//| (v1.10: server_time = broker clock, for the live DST-safe offset) |
//+------------------------------------------------------------------+
void WriteAccountInfo()
{
   string filename = "mt4_account.json";
   int handle = FileOpen(filename, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE) return;

   double startBalance = 0.0;
   for(int i = 0; i < OrdersHistoryTotal(); i++)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
      {
         if(OrderType() == 6) // OP_BALANCE (deposit/withdrawal)
         {
            double p = OrderProfit();
            if(p > 0.0) // only positive deposits as starting base
            {
               startBalance += p;
            }
         }
      }
   }
   if(startBalance <= 0.0) {
      // Fallback: current balance minus floating profit if history lacks deposits
      startBalance = AccountBalance() - AccountProfit();
   }

   string content = "{\"balance\":" + DoubleToString(AccountBalance(), 2) +
                    ",\"equity\":" + DoubleToString(AccountEquity(), 2) +
                    ",\"profit\":" + DoubleToString(AccountProfit(), 2) +
                    ",\"free_margin\":" + DoubleToString(AccountFreeMargin(), 2) +
                    ",\"leverage\":" + IntegerToString(AccountLeverage()) +
                    ",\"currency\":\"" + AccountCurrency() + "\"" +
                    ",\"name\":\"" + AccountName() + "\"" +
                    ",\"company\":\"" + AccountCompany() + "\"" +
                    ",\"start_balance\":" + DoubleToString(startBalance, 2) +
                    ",\"server_time\":" + IntegerToString((int)TimeCurrent()) + "}";

   FileWriteString(handle, content);
   FileClose(handle);
}
//+------------------------------------------------------------------+
