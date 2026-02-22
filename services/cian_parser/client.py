import asyncio
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor
from cianparser import CianParser

class CianCaptchaError(Exception):
    """Exception raised when CIAN returns captcha redirect"""
    pass

class CianClient:
    """Обертка над библиотекой cianparser для асинхронного использования"""
    EXECUTOR = ThreadPoolExecutor(max_workers=1)  # Один парсер за раз
    
    def __init__(self):
        self.parser = None
        self._initialized = False
        self._location = None
    
    def _init_parser(self, location: str = "Москва"):
        """Инициализирует парсер с указанным location"""
        if not self._initialized or self._location != location:
            self.parser = CianParser(location=location)
            self._location = location
            self._initialized = True
    
    async def _run_in_executor(self, func, *args, **kwargs):
        """Запускает синхронную функцию в executor"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.EXECUTOR, lambda: func(*args, **kwargs))
    
    def _convert_filters(self, **filters) -> Dict[str, Any]:
        """Конвертирует внутренние фильтры в формат cianparser"""
        params = {}
        
        # Регион (1 = Москва по умолчанию)
        region_id = filters.get('region_id', 1)
        location = 'Москва'  # По умолчанию Москва
        if region_id == 1:
            location = 'Москва'
        # Можно добавить другие регионы при необходимости
        params['location'] = location
        
        # Тип сделки (аренда)
        params['deal_type'] = 'rent_long'  # Долгосрочная аренда
        
        # Комнаты
        rooms = filters.get('rooms', [1, 2])
        if rooms:
            # cianparser принимает кортеж комнат
            if len(rooms) == 1:
                params['rooms'] = rooms[0]
            else:
                params['rooms'] = tuple(rooms)
        
        # Дополнительные настройки
        max_pages = filters.get('max_pages', 1)
        additional_settings = {
            'start_page': 1,
            'end_page': max_pages
        }
        
        # Цена - добавляем в additional_settings
        # В cianparser параметры цены называются min_price и max_price
        min_price = filters.get('min_price')
        max_price = filters.get('max_price')
        if min_price:
            additional_settings['min_price'] = min_price
        if max_price:
            additional_settings['max_price'] = max_price
        
        # Площадь - добавляем в additional_settings
        area_min = filters.get('area_min')
        if area_min:
            additional_settings['min_flat_area'] = area_min
        
        params['additional_settings'] = additional_settings
        
        return params
    
    def _convert_offer(self, offer_data: Dict[str, Any]) -> Dict[str, Any]:
        """Конвертирует объявление из формата cianparser в наш формат"""
        try:
            # Маппинг полей cianparser в наш формат
            # Структура может отличаться, поэтому проверяем разные варианты
            cian_id = offer_data.get("cian_id") or offer_data.get("id") or offer_data.get("offer_id")
            if not cian_id:
                return {}
            
            converted = {
                "id": cian_id,
                "cian_id": cian_id,
                "fullUrl": offer_data.get("url", "") or offer_data.get("link", ""),
                "url": offer_data.get("url", "") or offer_data.get("link", ""),
                "price_rub": offer_data.get("price") or offer_data.get("price_rub"),
                "bargainTerms": {
                    "price": offer_data.get("price") or offer_data.get("price_rub")
                },
                "roomsCount": offer_data.get("rooms") or offer_data.get("roomsCount", 1),
                "rooms": offer_data.get("rooms") or offer_data.get("roomsCount", 1),
                "totalArea": offer_data.get("area") or offer_data.get("totalArea") or offer_data.get("square", 0),
                "area_sqm": offer_data.get("area") or offer_data.get("totalArea") or offer_data.get("square", 0),
                "floorNumber": offer_data.get("floor"),
                "floor": offer_data.get("floor"),
                "floorsCount": offer_data.get("floors_total") or offer_data.get("floorsCount"),
                "floors_total": offer_data.get("floors_total") or offer_data.get("floorsCount"),
                "geo": {
                    "userInput": offer_data.get("address", "") or offer_data.get("location", ""),
                    "address": offer_data.get("address", "") or offer_data.get("location", ""),
                    "coordinates": {
                        "lat": offer_data.get("latitude") or offer_data.get("lat"),
                        "lng": offer_data.get("longitude") or offer_data.get("lng")
                    }
                },
                "address": offer_data.get("address", "") or offer_data.get("location", ""),
                "lat": offer_data.get("latitude") or offer_data.get("lat"),
                "lng": offer_data.get("longitude") or offer_data.get("lng"),
                "photos": []
            }
            
            # Фото
            photos = offer_data.get("photos", []) or offer_data.get("images", [])
            if isinstance(photos, list):
                converted["photos"] = [
                    {"fullUrl": photo if isinstance(photo, str) else photo.get("url", "")}
                    for photo in photos[:5]  # Максимум 5 фото
                    if photo
                ]
            
            return converted
            
        except Exception as e:
            print(f"Ошибка конвертации объявления: {e}, данные: {offer_data}")
            return {}
    
    async def search_offers(
        self,
        region_id: int = 1,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        rooms: List[int] = [1, 2],
        area_min: Optional[int] = None,
        floor_pref: Optional[str] = None,
        renovation: Optional[str] = None,
        page: int = 1,
        max_pages: Optional[int] = None
    ) -> Dict[str, Any]:
        """Поиск объявлений через cianparser"""
        # Конвертируем фильтры для получения location
        filter_params = self._convert_filters(
            region_id=region_id,
            min_price=min_price,
            max_price=max_price,
            rooms=rooms,
            area_min=area_min,
            floor_pref=floor_pref,
            renovation=renovation,
            max_pages=1  # Временное значение, обновим ниже
        )
        
        # Получаем location
        location = filter_params.pop('location', 'Москва')
        
        # Инициализируем парсер с правильным location
        self._init_parser(location=location)
        
        # Используем max_pages если передан, иначе page
        pages_to_fetch = max_pages if max_pages is not None else page
        
        # Обновляем max_pages в additional_settings
        filter_params['additional_settings']['end_page'] = pages_to_fetch
        
        try:
            # Запускаем синхронный парсер в executor
            def parse_offers():
                try:
                    # Метод get_flats из cianparser принимает:
                    # deal_type, rooms, with_saving_csv, with_extra_data, additional_settings
                    # location уже установлен при инициализации парсера
                    deal_type = filter_params.get('deal_type', 'rent_long')
                    rooms = filter_params.get('rooms', (1, 2))
                    additional_settings = filter_params.get('additional_settings', {
                        "start_page": 1,
                        "end_page": 1
                    })
                    
                    # Убеждаемся, что start_page и end_page установлены
                    if "start_page" not in additional_settings:
                        additional_settings["start_page"] = 1
                    if "end_page" not in additional_settings:
                        additional_settings["end_page"] = 1
                    
                    offers = self.parser.get_flats(
                        deal_type=deal_type,
                        rooms=rooms,
                        additional_settings=additional_settings
                    )
                    return offers if offers else []
                except Exception as e:
                    error_msg = str(e).lower()
                    if "captcha" in error_msg or "капча" in error_msg or "block" in error_msg:
                        raise CianCaptchaError(f"CIAN заблокировал запрос: {e}")
                    raise
            
            offers = await self._run_in_executor(parse_offers)
            
            # Конвертируем в наш формат
            converted_offers = []
            for offer in offers:
                converted = self._convert_offer(offer)
                if converted and converted.get("id"):
                    converted_offers.append(converted)
            
            return {
                "data": {
                    "offersSerialized": converted_offers
                }
            }
            
        except CianCaptchaError:
            raise
        except Exception as e:
            raise CianCaptchaError(f"Ошибка при парсинге Циан через cianparser: {e}")
    
    async def get_all_offers(self, max_pages: int = 3, **kwargs) -> List[Dict[str, Any]]:
        """Получает все объявления с нескольких страниц"""
        all_offers = []
        
        # cianparser сам обрабатывает страницы через additional_settings
        # Делаем один запрос со всеми страницами
        try:
            kwargs['max_pages'] = max_pages
            
            data = await self.search_offers(**kwargs)
            offers = data.get("data", {}).get("offersSerialized", [])
            
            if offers:
                all_offers.extend(offers)
                print(f"Найдено {len(offers)} объявлений")
            else:
                print("Объявления не найдены")
                
        except CianCaptchaError as e:
            print(f"⚠️  Циан заблокировал запрос: {e}")
        except Exception as e:
            print(f"Ошибка при получении объявлений: {e}")
        
        return all_offers
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def close(self):
        """Закрывает парсер (если нужно)"""
        # cianparser может не требовать явного закрытия
        pass
