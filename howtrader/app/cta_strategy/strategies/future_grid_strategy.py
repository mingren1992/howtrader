from howtrader.app.cta_strategy import (
    CtaTemplate,
    StopOrder,
    TickData,
    BarData,
    TradeData,
    OrderData
)

from howtrader.app.cta_strategy.engine import CtaEngine
from howtrader.trader.event import EVENT_TIMER
from howtrader.event import Event
from howtrader.trader.object import Status
from howtrader.trader.object import GridPositionCalculator

TIMER_INTERVAL = 15
PROFIT_TIMER_INTERVAL = 15  #
STOP_TIMER_INTERVAL = 60


class FutureGridStrategy(CtaTemplate):
    """"""
    author = "51bitquant"

    grid_step = 1.0  # 网格间隙.
    fixed_size = 0.5  # 每次下单的头寸.
    max_pos_size = 7.0  # 最大的头寸数.
    trailing_stop_multiplier = 2.0
    stop_minutes = 15.0   # 休息时间.

    profit_orders_counts = 4  # 出现多少个网格的时候，会考虑止盈.

    parameters = ["grid_step", "fixed_size", "max_pos_size", "profit_orders_counts", "trailing_stop_multiplier"]

    avg_price = 0.0
    current_pos = 0.0
    variables = ["avg_price", "current_pos"]

    def __init__(self, cta_engine: CtaEngine, strategy_name, vt_symbol, setting):
        """"""
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)

        self.position_calculator = GridPositionCalculator(grid_step=self.grid_step)  # 计算仓位用的对象
        self.current_pos = self.position_calculator.pos
        self.avg_price = self.position_calculator.avg_price

        self.timer_interval = 0  # 及时timer.
        self.profit_order_interval = 0
        self.stop_order_interval = 0
        self.stop_strategy_interval = 0

        self.long_orders = []  # 所有的long orders.
        self.short_orders = []  # 所有的short orders.
        self.profit_orders = []  # profit orders.
        self.stop_orders = []  # stop orders.

        self.trigger_stop_loss = False  # 是否触发止损。

        self.last_filled_order: OrderData = None

        self.tick: TickData = None

    def on_init(self):
        """
        Callback when strategy is inited.
        """
        self.write_log("策略初始化")

    def on_start(self):
        """
        Callback when strategy is started.
        """
        self.write_log("策略启动")
        self.cta_engine.event_engine.register(EVENT_TIMER, self.process_timer_event)

    def on_stop(self):
        """
        Callback when strategy is stopped.
        """
        self.write_log("策略停止")
        self.cta_engine.event_engine.unregister(EVENT_TIMER, self.process_timer_event)

    def process_timer_event(self, event: Event):


        if self.trigger_stop_loss:
            self.stop_strategy_interval += 1  # 如果触发了止损，然后就会开始计时.

        self.timer_interval += 1
        if self.timer_interval > TIMER_INTERVAL:
            self.timer_interval = 0

            if self.tick is None:
                return

            # 仓位为零的时候.
            if len(self.long_orders) == 0 and len(self.short_orders) == 0 and abs(self.position_calculator.pos) < self.fixed_size:

                if self.trigger_stop_loss:
                    # 如果触发了止损就需要休息一段时间.
                    if self.stop_order_interval < self.stop_minutes * 60:
                        return
                    else:
                        self.stop_order_interval = 0
                        self.trigger_stop_loss = False


                buy_price = self.tick.bid_price_1 - self.grid_step / 2
                sell_price = self.tick.bid_price_1 + self.grid_step / 2
                long_ids = self.buy(buy_price, self.fixed_size)
                short_ids = self.short(sell_price, self.fixed_size)

                self.long_orders.extend(long_ids)
                self.short_orders.extend(short_ids)

            elif len(self.long_orders) == 0 or len(self.short_orders) == 0:

                for vt_id in (self.long_orders + self.short_orders):
                    self.cancel_order(vt_id)

                if self.last_filled_order is None:
                    return

                if abs(self.position_calculator.pos) < self.fixed_size:
                    return

                step = self.get_step()
                buy_price = self.last_filled_order.price - step * self.grid_step
                sell_price = self.last_filled_order.price + step * self.grid_step

                buy_price = min(self.tick.bid_price_1, buy_price)
                sell_price = max(self.tick.ask_price_1, sell_price)
                long_ids = self.buy(buy_price, self.fixed_size)
                short_ids = self.short(sell_price, self.fixed_size)

                self.long_orders.extend(long_ids)
                self.short_orders.extend(short_ids)

        self.profit_order_interval += 1

        if self.profit_order_interval >= PROFIT_TIMER_INTERVAL:
            self.profit_order_interval = 0

            if abs(self.position_calculator.pos) >= self.profit_orders_counts * self.fixed_size and len(self.profit_orders) == 0:

                if self.position_calculator.pos > 0:
                    price = max(self.tick.ask_price_1 * (1 + 0.0001), self.position_calculator.avg_price + self.grid_step)
                    order_ids = self.sell(price, abs(self.position_calculator.pos))
                    self.profit_orders.extend(order_ids)
                elif self.position_calculator.pos < 0:
                    price = min(self.tick.bid_price_1 * (1 - 0.0001), self.position_calculator.avg_price - self.grid_step)
                    order_ids = self.cover(price, abs(self.position_calculator.pos))
                    self.profit_orders.extend(order_ids)

        self.stop_order_interval += 1
        if self.stop_order_interval >= STOP_TIMER_INTERVAL:
            self.stop_order_interval = 0

            for vt_id in self.stop_orders:
                self.cancel_order(vt_id)

            # 如果仓位达到最大值的时候.
            if abs(self.position_calculator.pos) >= self.max_pos_size * self.fixed_size:

                if self.last_filled_order:
                    if self.position_calculator.pos > 0:
                        if self.tick.bid_price_1 < self.last_filled_order.price - self.trailing_stop_multiplier * self.grid_step:
                            vt_ids = self.sell(self.tick.bid_price_1, abs(self.position_calculator.pos))
                            self.stop_orders.extend(vt_ids)
                            self.trigger_stop_loss = True  # 触发止损
                    elif self.position_calculator.pos < 0:
                        if self.tick.ask_price_1 > self.last_filled_order.price + self.trailing_stop_multiplier * self.grid_step:
                            vt_ids = self.cover(self.tick.ask_price_1, abs(self.position_calculator.pos))
                            self.stop_orders.extend(vt_ids)
                            self.trigger_stop_loss = True  # 触发止损

                else:
                    if self.position_calculator.pos > 0:
                        if self.tick.bid_price_1 < self.position_calculator.avg_price - self.max_pos_size * self.grid_step:
                            vt_ids = self.sell(self.tick.bid_price_1, abs(self.position_calculator.pos))
                            self.stop_orders.extend(vt_ids)
                            self.trigger_stop_loss = True  # 触发止损

                    elif self.position_calculator.pos < 0:
                        if self.tick.ask_price_1 > self.position_calculator.avg_price + self.max_pos_size * self.grid_step:
                            vt_ids = self.cover(self.tick.ask_price_1, abs(self.position_calculator.pos))
                            self.stop_orders.extend(vt_ids)
                            self.trigger_stop_loss = True  # 触发止损

    def on_tick(self, tick: TickData):
        """
        Callback of new tick data update.
        """
        self.tick = tick

    def on_bar(self, bar: BarData):
        """
        Callback of new bar data update.
        """
        pass

    def get_step(self) -> int:

        pos = abs(self.position_calculator.pos)

        if pos < 3 * self.fixed_size:
            return 1

        elif pos < 5 * self.fixed_size:
            return 2

        elif pos < 7 * self.fixed_size:
            return 4

        return 6

    def on_order(self, order: OrderData):
        """
        Callback of new order data update.
        """
        self.position_calculator.update_position(order)

        self.current_pos = self.position_calculator.pos
        self.avg_price = self.position_calculator.avg_price

        if order.status == Status.ALLTRADED and order.vt_orderid in (self.long_orders + self.short_orders):

            if order.vt_orderid in self.long_orders:
                self.long_orders.remove(order.vt_orderid)

            if order.vt_orderid in self.short_orders:
                self.short_orders.remove(order.vt_orderid)

            self.last_filled_order = order

            for ids in (self.long_orders + self.short_orders + self.profit_orders):
                self.cancel_order(ids)

            if abs(self.position_calculator.pos) < self.fixed_size:
                return

            step = self.get_step()

            # tick 存在且仓位数量还没有达到设置的最大值.
            if self.tick and abs(self.position_calculator.pos) < self.max_pos_size * self.fixed_size:
                buy_price = order.price - step * self.grid_step
                sell_price = order.price + step * self.grid_step

                buy_price = min(self.tick.bid_price_1 * (1 - 0.0001), buy_price)
                sell_price = max(self.tick.ask_price_1 * (1 + 0.0001), sell_price)

                long_ids = self.buy(buy_price, self.fixed_size)
                short_ids = self.short(sell_price, self.fixed_size)

                self.long_orders.extend(long_ids)
                self.short_orders.extend(short_ids)

        if order.status == Status.ALLTRADED and order.vt_orderid in self.profit_orders:
            self.profit_orders.remove(order.vt_orderid)
            if abs(self.position_calculator.pos) < self.fixed_size:
                self.cancel_all()

        if not order.is_active():
            if order.vt_orderid in self.long_orders:
                self.long_orders.remove(order.vt_orderid)

            elif order.vt_orderid in self.short_orders:
                self.short_orders.remove(order.vt_orderid)

            elif order.vt_orderid in self.profit_orders:
                self.profit_orders.remove(order.vt_orderid)

            elif order.vt_orderid in self.stop_orders:
                self.stop_orders.remove(order.vt_orderid)

        self.put_event()

    def on_trade(self, trade: TradeData):
        """
        Callback of new trade data update.
        """
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder):
        """
        Callback of stop order update.
        """
        pass
